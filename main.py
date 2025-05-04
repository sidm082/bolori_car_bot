import os
import sqlite3
import logging
import asyncio
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes
)
from dotenv import load_dotenv

# تنظیمات لاگ‌گیری
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# بارگذاری متغیرهای محیطی
load_dotenv()

# خواندن توکن از محیط
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    logger.error("BOT_TOKEN not found in .env file")
    raise ValueError("لطفاً توکن ربات را در فایل .env تنظیم کنید. برای اطلاعات بیشتر، مستندات را بررسی کنید.")

# تنظیمات کانال
CHANNEL_URL = "https://t.me/bolori_car"
CHANNEL_ID = "@bolori_car"
CHANNEL_USERNAME = "bolori_car"

# مراحل گفتگو
AD_TITLE, AD_DESCRIPTION, AD_PRICE, AD_PHOTOS, AD_PHONE = range(5)

# --- توابع دیتابیس ---
def get_db_connection():
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY, 
                     joined TEXT, 
                     phone TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS ads
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     title TEXT,
                     description TEXT,
                     price TEXT,
                     photos TEXT,
                     status TEXT DEFAULT 'pending',
                     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY(user_id) REFERENCES users(user_id))''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                    (user_id INTEGER PRIMARY KEY)''')
        
        # ایجاد شاخص‌ها برای بهبود عملکرد
        c.execute('CREATE INDEX IF NOT EXISTS idx_ads_status ON ads(status)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ads_user_id ON ads(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)')
        
        # ادمین پیش‌فرض
        initial_admin_id = 5677216420
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (initial_admin_id,))
        conn.commit()
    finally:
        conn.close()

def load_admin_ids():
    conn = get_db_connection()
    try:
        c = conn.cursor()
        admins = c.execute('SELECT user_id FROM admins').fetchall()
        return [admin['user_id'] for admin in admins]
    finally:
        conn.close()

# --- تابع کمکی برای مدیریت نرخ ارسال ---
async def send_message_with_rate_limit(bot, chat_id, text=None, photo=None, reply_markup=None):
    try:
        if photo:
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        await asyncio.sleep(1)  # تأخیر 1 ثانیه برای رعایت نرخ
    except Exception as e:
        logger.error(f"Error sending message/photo: {e}")

# --- توابع اصلی ربات ---
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Membership check failed for user {user_id}: {e}")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ عضویت در کانال", url=CHANNEL_URL)],
            [InlineKeyboardButton("🔄 بررسی عضویت", callback_data="check_membership")]
        ])
        await update.effective_message.reply_text(
            "⚠️ خطایی در بررسی عضویت رخ داد. لطفاً در کانال عضو شوید و دوباره تلاش کنید:",
            reply_markup=keyboard
        )
        return False

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            await query.edit_message_text("✅ عضویت شما تایید شد! حالا می‌توانید ادامه دهید.")
            await start(update, context)
        else:
            await query.answer("شما هنوز عضو نشدید!", show_alert=True)
    except Exception as e:
        logger.error(f"Callback membership check failed for user {user_id}: {e}")
        await query.answer("خطا در بررسی عضویت. لطفاً دوباره تلاش کنید.", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await check_membership(update, context):
        buttons = [
            [InlineKeyboardButton("➕ ثبت آگهی", callback_data="post_ad")],
            [InlineKeyboardButton("✏️ ویرایش اطلاعات", callback_data="edit_info")],
            [InlineKeyboardButton("🗂️ نمایش آگهی‌ها", callback_data="show_ads")]
        ]
        
        if user.id in ADMIN_ID:
            buttons.append([InlineKeyboardButton("👨‍💼 پنل ادمین", callback_data="admin_panel")])
            buttons.append([InlineKeyboardButton("📊 آمار کاربران", callback_data="stats")])
        
        welcome_text = (
            f"سلام {user.first_name} عزیز! 👋\n\n"
            "به ربات رسمی ثبت آگهی خودرو *اتوگالری بلوری* خوش آمدید.از طریق این ربات می‌توانید:\n  آگهی فروش خودروی خود را به‌صورت مرحله‌به‌مرحله ثبت کنید\n  آگهی‌های ثبت‌شده را مشاهده و جست‌وجو نمایید\n لطفاً یکی از گزینه‌های زیر را انتخاب کنید:\n\n"
        )
        
        await update.effective_message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        
        conn = get_db_connection()
        try:
            with conn:
                conn.execute(
                    'INSERT OR REPLACE INTO users (user_id, joined) VALUES (?, ?)',
                    (user.id, datetime.now().isoformat())
                )
        except sqlite3.Error as e:
            logger.error(f"Database error in start: {e}")
            await update.effective_message.reply_text("❌ خطایی در ثبت اطلاعات شما در سیستم رخ داد.")
        finally:
            conn.close()

async def start_edit_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    
    if not await check_membership(update, context):
        await message.reply_text("⚠️ لطفا ابتدا در کانال عضو شوید!")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        user_data = conn.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,)).fetchone()
        current_phone = user_data['phone'] if user_data and user_data['phone'] else "ثبت نشده"
        
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📞 ارسال شماره", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await message.reply_text(
            f"📞 شماره تلفن فعلی شما: {current_phone}\n"
            "لطفاً شماره تلفن جدید را با زدن دکمه زیر یا تایپ دستی ارسال کنید:",
            reply_markup=keyboard
        )
        return AD_PHONE
    except sqlite3.Error as e:
        logger.error(f"Database error in start_edit_info: {e}")
        await message.reply_text("❌ خطایی در بررسی اطلاعات رخ داد.")
        return ConversationHandler.END
    finally:
        conn.close()

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    
    if not await check_membership(update, context):
        await message.reply_text("⚠️ لطفا ابتدا در کانال عضو شوید!")
        return ConversationHandler.END
    
    context.user_data['ad'] = {'photos': []}
    await message.reply_text("📝 لطفاً برند و مدل خودروی خود را وارد نمایید.(مثال: پژو ۲۰۶ تیپ ۲، کیا سراتو، تویوتا کمری و …):")
    return AD_TITLE

async def receive_ad_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.effective_message.reply_text("لطفاً عنوان معتبر وارد کنید.")
        return AD_TITLE
    
    context.user_data['ad']['title'] = title
    await update.effective_message.reply_text("لطفا اطلاعات خودرو شامل رنگ، کارکرد، وضعیت بدنه، وضعیت فنی و غیره را وارد نمایید.")
    return AD_DESCRIPTION

async def receive_ad_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.effective_message.reply_text("لطفاً توضیحات معتبر وارد کنید.")
        return AD_DESCRIPTION
    
    context.user_data['ad']['description'] = description
    await update.effective_message.reply_text("لطفا قیمت خودرو را به تومان وارد کنید:")
    return AD_PRICE

async def receive_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = update.message.text.strip()
    if not price.replace(",", "").isdigit():
        await update.effective_message.reply_text("لطفاً قیمت را به صورت عددی و به تومان وارد کنید.")
        return AD_PRICE
    
    context.user_data['ad']['price'] = price
    await update.effective_message.reply_text(
        "لطفا عکس خودرو را ارسال کنید (حداکثر 5 تصویر) (یا 'تمام' برای اتمام یا 'هیچ' اگر عکسی ندارید):"
    )
    return AD_PHOTOS

async def receive_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad = context.user_data['ad']
    
    if update.message.text and update.message.text.lower() == "هیچ":
        ad['photos'] = []
        return await request_phone(update, context)
    elif update.message.photo:
        if len(ad['photos']) >= 5:
            await update.effective_message.reply_text(
                "⚠️ شما حداکثر 5 تصویر می‌توانید ارسال کنید. لطفاً 'تمام' را بنویسید."
            )
            return AD_PHOTOS
        ad['photos'].append(update.message.photo[-1].file_id)
        await update.effective_message.reply_text(
            f"عکس دریافت شد ({len(ad['photos'])}/5). برای ارسال عکس دیگر، عکس بفرستید یا 'تمام' را ارسال کنید."
        )
        return AD_PHOTOS
    elif update.message.text and update.message.text.lower() == "تمام":
        if not ad['photos']:
            await update.effective_message.reply_text("لطفاً حداقل یک عکس ارسال کنید یا 'هیچ' را بفرستید.")
            return AD_PHOTOS
        return await request_phone(update, context)
    else:
        await update.effective_message.reply_text(
            "لطفاً یک عکس ارسال کنید یا 'تمام' یا 'هیچ' را بنویسید."
        )
        return AD_PHOTOS

async def request_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        user_data = conn.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,)).fetchone()
        
        if user_data and user_data['phone']:
            context.user_data['ad']['phone'] = user_data['phone']
            return await save_ad(update, context)
        
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📞 ارسال شماره", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await update.effective_message.reply_text(
            "📞 لطفاً شماره تلفن خود را برای ثبت آگهی با زدن دکمه زیر ارسال کنید:",
            reply_markup=keyboard
        )
        return AD_PHONE
    except sqlite3.Error as e:
        logger.error(f"Database error in request_phone: {e}")
        await update.effective_message.reply_text("❌ خطایی در بررسی اطلاعات رخ داد.")
        return ConversationHandler.END
    finally:
        conn.close()

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = None
    
    if update.message.contact:
        phone = update.message.contact.phone_number
    elif update.message.text:
        phone = update.message.text.strip()
    
    phone_pattern = r'^(\+98|0)?9\d{9}$'
    cleaned_phone = phone.replace('-', '').replace(' ', '')
    if not phone or not re.match(phone_pattern, cleaned_phone):
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📞 ارسال شماره", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.effective_message.reply_text(
            "⚠️ لطفاً یک شماره تلفن معتبر (مثل +989121234567 یا 09121234567) وارد کنید:",
            reply_markup=keyboard
        )
        return AD_PHONE
    
    if cleaned_phone.startswith('0'):
        cleaned_phone = '+98' + cleaned_phone[1:]
    elif not cleaned_phone.startswith('+'):
        cleaned_phone = '+98' + cleaned_phone
    
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                'INSERT OR REPLACE INTO users (user_id, phone) VALUES (?, ?)',
                (user_id, cleaned_phone)
            )
        
        if 'ad' in context.user_data and context.user_data['ad']:
            context.user_data['ad']['phone'] = cleaned_phone
            await update.effective_message.reply_text(
                "✅ شماره تلفن با موفقیت ثبت شد. آگهی شما در حال ارسال برای تأیید است...",
                reply_markup=ReplyKeyboardRemove()
            )
            return await save_ad(update, context)
        else:
            await update.effective_message.reply_text(
                "✅ شماره تلفن با موفقیت به‌روزرسانی شد.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
    except sqlite3.Error as e:
        logger.error(f"Database error in receive_phone: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در ثبت اطلاعات رخ داد. لطفاً دوباره تلاش کنید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return AD_PHONE
    finally:
        conn.close()

async def save_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad = context.user_data['ad']
    user_id = update.effective_user.id
    
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO ads 
                (user_id, title, description, price, photos, created_at) 
                VALUES (?, ?, ?, ?, ?, ?)''',
                (
                    user_id,
                    ad['title'],
                    ad['description'],
                    ad['price'],
                    ','.join(ad['photos']) if ad['photos'] else '',
                    datetime.now().isoformat()
                )
            )
            ad_id = cursor.lastrowid
        
        for admin_id in ADMIN_ID:
            try:
                await send_message_with_rate_limit(
                    context.bot,
                    admin_id,
                    text=f"📢 آگهی جدید ثبت شد:\nعنوان: {ad['title']}\nID: {ad_id}\nلطفاً در پنل ادمین بررسی کنید."
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        await update.effective_message.reply_text(
            "از اعتماد شما سپاسگزاریم.✅ آگهی با موفقیت ثبت شد و در انتظار تأیید مدیر است.\n"
            "می‌توانید از منوی اصلی برای ثبت آگهی جدید استفاده کنید."
        )
        context.user_data.clear()  # پاکسازی داده‌های موقت
        return ConversationHandler.END
    except sqlite3.Error as e:
        logger.error(f"Database error in save_ad: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در ثبت آگهی رخ داد. لطفاً دوباره تلاش کنید."
        )
        return ConversationHandler.END
    finally:
        conn.close()

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    
    if update.effective_user.id not in ADMIN_ID:
        await message.reply_text("❌ دسترسی ممنوع!")
        return
    
    page = context.user_data.get('admin_page', 1)
    items_per_page = 5
    status_filter = context.user_data.get('admin_status_filter', 'pending')
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        total_ads = cursor.execute(
            'SELECT COUNT(*) FROM ads WHERE status = ?', 
            (status_filter,)
        ).fetchone()[0]
        
        total_pages = max(1, (total_ads + items_per_page - 1) // items_per_page)
        page = max(1, min(page, total_pages))
        context.user_data['admin_page'] = page
        
        offset = (page - 1) * items_per_page
        ads = cursor.execute(
            '''SELECT * FROM ads 
            WHERE status = ? 
            ORDER BY created_at DESC 
            LIMIT ? OFFSET ?''',
            (status_filter, items_per_page, offset)
        ).fetchall()
        
        if not ads:
            await send_message_with_rate_limit(
                context.bot,
                update.effective_chat.id,
                text=f"هیچ آگهی‌ای با وضعیت '{status_filter}' یافت نشد.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 تغییر وضعیت", callback_data="change_status")],
                    [InlineKeyboardButton("🏠 بازگشت", callback_data="admin_exit")]
                ])
            )
            return
        
        for ad in ads:
            user_info = cursor.execute(
                'SELECT phone FROM users WHERE user_id = ?', 
                (ad['user_id'],)
            ).fetchone()
            
            phone = user_info['phone'] if user_info else "نامشخص"
            
            try:
                user = await context.bot.get_chat(ad['user_id'])
                username = user.username or f"{user.first_name} {user.last_name or ''}"
            except Exception:
                user = None
                username = "نامشخص"
            
            ad_text = (
                f"🆔 آگهی: {ad['id']}\n"
                f"👤 کاربر: {username}\n"
                f"📞 شماره: {phone}\n"
                f"📌 عنوان: {ad['title']}\n"
                f"💬 توضیحات: {ad['description']}\n"
                f"💰 قیمت: {ad['price']}\n"
                f"📅 تاریخ: {ad['created_at']}\n"
                f"📸 تصاویر: {'دارد' if ad['photos'] else 'ندارد'}\n"
                f"📊 وضعیت: {ad['status']}"
            )
            
            buttons = [
                [
                    InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{ad['id']}"),
                    InlineKeyboardButton("❌ رد", callback_data=f"reject_{ad['id']}")
                ],
                [InlineKeyboardButton("🖼️ نمایش تصاویر", callback_data=f"show_photos_{ad['id']}")]
            ]
            
            if ad['photos']:
                photos = ad['photos'].split(',')
                for photo in photos[:5]:
                    await send_message_with_rate_limit(
                        context.bot,
                        update.effective_chat.id,
                        text=ad_text,
                        photo=photo,
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
            else:
                await send_message_with_rate_limit(
                    context.bot,
                    update.effective_chat.id,
                    text=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
        
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ صفحه قبلی", callback_data=f"page_{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("➡️ صفحه بعدی", callback_data=f"page_{page+1}"))
        
        nav_buttons_row = [nav_buttons] if nav_buttons else []
        
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text=f"📄 صفحه {page} از {total_pages} (وضعیت: {status_filter})",
            reply_markup=InlineKeyboardMarkup(
                nav_buttons_row + [
                    [InlineKeyboardButton("🔄 تغییر وضعیت", callback_data="change_status")],
                    [InlineKeyboardButton("🏠 بازگشت", callback_data="admin_exit")]
                ]
            )
        )
    except Exception as e:
        logger.error(f"Error in admin_panel: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آگهی‌ها رخ داد."
        )
    finally:
        conn.close()

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی ممنوع!")
        return
    
    action, ad_id = query.data.split('_')
    ad_id = int(ad_id)
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        ad = cursor.execute(
            'SELECT user_id, title, status FROM ads WHERE id = ?', 
            (ad_id,)
        ).fetchone()
        
        if not ad:
            await query.message.reply_text("❌ آگهی یافت نشد!")
            return
        
        if action == "approve":
            new_status = "approved"
            user_message = f"✅ آگهی شما با عنوان '{ad['title']}' تأیید شد."
        elif action == "reject":
            new_status = "rejected"
            user_message = f"❌ آگهی شما با عنوان '{ad['title']}' رد شد."
        else:
            return
        
        cursor.execute(
            'UPDATE ads SET status = ? WHERE id = ?',
            (new_status, ad_id)
        )
        conn.commit()
        
        try:
            await send_message_with_rate_limit(
                context.bot,
                ad['user_id'],
                text=user_message
            )
        except Exception as e:
            logger.error(f"Failed to notify user {ad['user_id']}: {e}")
        
        await query.message.reply_text(f"وضعیت آگهی {ad_id} به '{new_status}' تغییر یافت.")
        await admin_panel(update, context)
    except sqlite3.Error as e:
        logger.error(f"Database error in handle_admin_action: {e}")
        await query.message.reply_text("❌ خطایی در پردازش درخواست رخ داد.")
    finally:
        conn.close()

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی ممنوع!")
        return
    
    data = query.data
    
    if data.startswith("approve_") or data.startswith("reject_"):
        await handle_admin_action(update, context)
    elif data.startswith("page_"):
        context.user_data['admin_page'] = int(data.split('_')[1])
        await admin_panel(update, context)
    elif data == "change_status":
        buttons = [
            [InlineKeyboardButton("⏳ در انتظار", callback_data="status_pending")],
            [InlineKeyboardButton("✅ تأیید شده", callback_data="status_approved")],
            [InlineKeyboardButton("❌ رد شده", callback_data="status_rejected")],
            [InlineKeyboardButton("🏠 بازگشت", callback_data="admin_exit")]
        ]
        await query.message.reply_text(
            "📊 وضعیت مورد نظر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    elif data.startswith("status_"):
        context.user_data['admin_status_filter'] = data.split('_')[1]
        context.user_data['admin_page'] = 1
        await admin_panel(update, context)
    elif data.startswith("show_photos_"):
        ad_id = int(data.split('_')[2])
        conn = get_db_connection()
        try:
            ad = conn.execute(
                'SELECT photos FROM ads WHERE id = ?', 
                (ad_id,)
            ).fetchone()
            
            if ad and ad['photos']:
                photos = ad['photos'].split(',')
                for photo in photos[:5]:
                    await send_message_with_rate_limit(
                        context.bot,
                        update.effective_chat.id,
                        text=f"تصویر آگهی {ad_id}",
                        photo=photo
                    )
            else:
                await query.message.reply_text("📸 این آگهی تصویری ندارد.")
        except Exception as e:
            logger.error(f"Error in show_photos: {e}")
            await query.message.reply_text("❌ خطایی در نمایش تصاویر رخ داد.")
        finally:
            conn.close()
    elif data == "admin_exit":
        await query.message.reply_text("🏠 بازگشت به منوی اصلی.")
        await start(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)

async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    
    one_year_ago = datetime.now() - timedelta(days=365)
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        ads = cursor.execute(
            '''SELECT * FROM ads 
            WHERE status = 'approved' 
            AND datetime(created_at) >= ? 
            ORDER BY created_at DESC''',
            (one_year_ago.isoformat(),)
        ).fetchall()
        
        if not ads:
            await send_message_with_rate_limit(
                context.bot,
                update.effective_chat.id,
                text="هیچ آگهی تأیید شده‌ای وجود ندارد."
            )
            return
        
        for ad in ads:
            text = (
                f"📌 عنوان: {ad['title']}\n"
                f"💬 توضیحات: {ad['description']}\n"
                f"💰 قیمت: {ad['price']}\n"
                f"📅 تاریخ: {ad['created_at']}"
            )
            
            try:
                if ad['photos']:
                    photos = ad['photos'].split(',')
                    for photo in photos[:3]:
                        await send_message_with_rate_limit(
                            context.bot,
                            update.effective_chat.id,
                            text=text,
                            photo=photo
                        )
                else:
                    await send_message_with_rate_limit(
                        context.bot,
                        update.effective_chat.id,
                        text=text
                    )
            except Exception as e:
                logger.error(f"Failed to send ad {ad['id']}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in show_ads: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آگهی‌ها رخ داد."
        )
    finally:
        conn.close()

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    
    if update.effective_user.id not in ADMIN_ID:
        await message.reply l_text("❌ دسترسی ممنوع!")
        return
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        total_users = cursor.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        new_users_today = cursor.execute(
            'SELECT COUNT(*) FROM users WHERE date(joined) = date("now")'
        ).fetchone()[0]
        
        total_ads = cursor.execute('SELECT COUNT(*) FROM ads').fetchone()[0]
        pending_ads = cursor.execute(
            'SELECT COUNT(*) FROM ads WHERE status = "pending"'
        ).fetchone()[0]
        approved_ads = cursor.execute(
            'SELECT COUNT(*) FROM ads WHERE status = "approved"'
        ).fetchone()[0]
        
        total_admins = cursor.execute('SELECT COUNT(*) FROM admins').fetchone()[0]
        
        stats_text = (
            "📊 آمار ربات:\n\n"
            f"👥 کاربران کل: {total_users}\n"
            f"🆕 کاربران امروز: {new_users_today}\n\n"
            f"📝 آگهی‌های کل: {total_ads}\n"
            f"⏳ در انتظار تأیید: {pending_ads}\n"
            f"✅ تأیید شده: {approved_ads}\n\n"
            f"👨‍💼 تعداد ادمین‌ها: {total_admins}"
        )
        
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text=stats_text
        )
    except sqlite3.Error as e:
        logger.error(f"Database error in stats: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آمار رخ داد."
        )
    finally:
        conn.close()

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی ممنوع!")
        return
    
    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(
            "⚠️ لطفاً ID کاربر را وارد کنید:\n"
            "مثال: /add_admin 123456789"
        )
        return
    
    new_admin_id = int(args[0])
    if new_admin_id in ADMIN_ID:
        await update.effective_message.reply_text("⚠️ این کاربر قبلاً ادمین است.")
        return
    
    conn = get_db_connection()
    try:
        with conn:
            conn.execute('INSERT INTO admins (user_id) VALUES (?)', (new_admin_id,))
        
        ADMIN_ID.append(new_admin_id)
        await update.effective_message.reply_text(f"✅ کاربر با ID {new_admin_id} به ادمین‌ها اضافه شد.")
        
        try:
            await send_message_with_rate_limit(
                context.bot,
                new_admin_id,
                text=f"🎉 شما به عنوان ادمین ربات اتوگالری بلوری منصوب شدید!\n"
                     f"از دستور /admin برای دسترسی به پنل مدیریت استفاده کنید."
            )
        except Exception as e:
            logger.error(f"Failed to notify new admin {new_admin_id}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in add_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در افزودن ادمین رخ داد.")
    finally:
        conn.close()

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی ممنوع!")
        return
    
    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(
            "⚠️ لطفاً ID کاربر را وارد کنید:\n"
            "مثال: /remove_admin 123456789"
        )
        return
    
    admin_id_to_remove = int(args[0])
    if admin_id_to_remove not in ADMIN_ID:
        await update.effective_message.reply_text("⚠️ این کاربر ادمین نیست.")
        return
    
    if len(ADMIN_ID) <= 1ստեղնاسازي 1
    if admin_id_to_remove == update.effective_user.id:
        await update.effective_message.reply_text("⚠️ نمی‌توانید خودتان را از ادمین‌ها حذف کنید!")
        return
    
    if len(ADMIN_ID) <= 1:
        await update.effective_message.reply_text("⚠️ نمی‌توانید آخرین ادمین را حذف کنید!")
        return
    
    conn = get_db_connection()
    try:
        with conn:
            conn.execute('DELETE FROM admins WHERE user_id = ?', (admin_id_to_remove,))
        
        ADMIN_ID.remove(admin_id_to_remove)
        await update.effective_message.reply_text(f"✅ کاربر با ID {admin_id_to_remove} از ادمین‌ها حذف شد.")
        
        try:
            await send_message_with_rate_limit(
                context.bot,
                admin_id_to_remove,
                text="❌ دسترسی ادمین شما از ربات اتوگالری بلوری حذف شد."
            )
        except Exception as e:
            logger.error(f"Failed to notify removed admin {admin_id_to_remove}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in remove_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در حذف ادمین رخ داد.")
    finally:
        conn.close()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()  # پاکسازی داده‌های موقت
    await update.effective_message.reply_text(
        "❌ عملیات فعلی لغو شد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="⚠️ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید."
        )

# --- تنظیمات اصلی ربات ---
def main():
    # مقداردهی اولیه دیتابیس
    init_db()
    global ADMIN_ID
    ADMIN_ID = load_admin_ids()
    
    # ساخت اپلیکیشن ربات
    application = Application.builder().token(TOKEN).build()
    
    # غیرفعال کردن webhook و حذف به‌روزرسانی‌های در انتظار
    application.bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Webhook غیرفعال شد")
    
    # تنظیم هندلرهای گفتگو
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("post_ad", post_ad),
            CallbackQueryHandler(post_ad, pattern="^post_ad$"),
            CommandHandler("edit_info", start_edit_info),
            CallbackQueryHandler(start_edit_info, pattern="^edit_info$"),
        ],
        states={
            AD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_title)],
            AD_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_description)],
            AD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_price)],
            AD_PHOTOS: [
                MessageHandler(filters.PHOTO, receive_ad_photos),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_photos)
            ],
            AD_PHONE: [
                MessageHandler(filters.CONTACT, receive_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )
    
    # اضافه کردن هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(check_membership_callback, pattern="^check_membership$"))
    application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(approve|reject|page|status|show_photos|change_status|admin_exit|admin_panel)_"))
    application.add_handler(CallbackQueryHandler(stats, pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(show_ads, pattern="^show_ads$"))
    application.add_handler(CommandHandler("add_admin", add_admin))
    application.add_handler(CommandHandler("remove_admin", remove_admin))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_error_handler(error_handler)
    
    # اجرای ربات
    logger.info("🚀 Starting bot...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        timeout=10,
        close_loop=False
    )

if __name__ == "__main__":
    main()
