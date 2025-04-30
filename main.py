import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, ContextTypes
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

application = ApplicationBuilder() \
    .token os.getenv("BOT_TOKEN") \
    .post_init(post_init) \
    .post_stop(post_stop) \
    .build()

async def post_init(application):
    print("ربات با موفقیت راه‌اندازی شد!")
    
async def post_stop(application):
    print("ربات در حال توقف...")
    # میتوانید اینجا پیام به ادمین ارسال کنید
# تنظیم لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# بارگذاری متغیرهای محیطی
load_dotenv()

# خواندن توکن به‌صورت امن
def load_bot_token():
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN is not set and .env file not found. Check environment variables.")
        raise ValueError("BOT_TOKEN is not set in environment variables")
    
    token_length = len(token)
    if token_length < 30:
        logger.error("BOT_TOKEN is too short (length: %d)", token_length)
        raise ValueError("BOT_TOKEN is too short")
    
    if not token.isascii():
        logger.error("BOT_TOKEN contains non-ASCII characters")
        raise ValueError("BOT_TOKEN contains non-ASCII characters")
    
    if any(c.isspace() for c in token):
        logger.error("BOT_TOKEN contains whitespace")
        raise ValueError("BOT_TOKEN contains whitespace")
    
    return token

try:
    TOKEN = load_bot_token()
except ValueError as e:
    logger.critical("Failed to initialize bot: %s", e)
    exit(1)

# تنظیمات اولیه
CHANNEL_URL = "https://t.me/bolori_car"
CHANNEL_ID = "@bolori_car"
CHANNEL_USERNAME = "bolori_car"

# اتصال به دیتابیس
def get_db_connection():
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ایجاد جداول دیتابیس
conn = get_db_connection()
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, joined INTEGER DEFAULT 0, phone TEXT)''')
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
# افزودن ادمین اولیه (اختیاری، برای شروع)
initial_admin_id = 5677216420  # جایگزین با ID ادمین اولیه
c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (initial_admin_id,))
conn.commit()
conn.close()

# بارگذاری لیست ادمین‌ها از دیتابیس
def load_admin_ids():
    conn = get_db_connection()
    try:
        c = conn.cursor()
        admins = c.execute('SELECT user_id FROM admins').fetchall()
        return [admin['user_id'] for admin in admins]
    except sqlite3.Error as e:
        logger.error(f"Database error in load_admin_ids: {e}")
        return []
    finally:
        conn.close()

ADMIN_ID = load_admin_ids()

# مراحل ConversationHandler
AD_TITLE, AD_DESCRIPTION, AD_PRICE, AD_PHOTOS, AD_PHONE = range(1, 6)

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Membership check failed for user {user_id}: {e}")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ عضویت در کانال", url=CHANNEL_URL)],
            [InlineKeyboardButton("🔄 بررسی عضویت", callback_data="check_membership")]
        ])
        await update.effective_message.reply_text(
            "برای ادامه، لطفاً ابتدا در کانال عضو شوید و سپس روی «بررسی عضویت» بزنید:",
            reply_markup=keyboard
        )
        return False

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    try:
        member = await context.bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            await query.edit_message_text("✅ عضویت شما تایید شد! حالا می‌توانید ادامه دهید.")
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
            [InlineKeyboardButton("📊 آمار کاربران (ادمین)", callback_data="stats")],
            [InlineKeyboardButton("🗂️ نمایش تمامی آگهی‌ها", callback_data="show_ads")]
        ]
        if user.id in ADMIN_ID:
            buttons.append([InlineKeyboardButton("👨‍💼 پنل ادمین", callback_data="admin_panel")])
        welcome_text = (
            f"سلام {user.first_name} عزیز! 👋\n\n"
            "به *اتوگالری بلوری* خوش آمدید.\n\n"
            "از دکمه‌های زیر برای ادامه استفاده کنید:"
        )
        await update.effective_message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        conn = get_db_connection()
        try:
            with conn:
                conn.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user.id,))
        except sqlite3.Error as e:
            logger.error(f"Database error in start: {e}")
            await update.effective_message.reply_text("❌ خطایی در ثبت اطلاعات شما در سیستم رخ داد.")
        finally:
            conn.close()
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ عضویت در کانال", url=CHANNEL_URL)],
            [InlineKeyboardButton("🔄 بررسی عضویت", callback_data="check_membership")]
        ])
        await update.effective_message.reply_text(
            "⚠️ برای استفاده از ربات ابتدا باید در کانال عضو شوید:\n",
            reply_markup=keyboard
        )

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not await check_membership(update, context):
        await message.reply_text("⚠️ لطفا ابتدا در کانال عضو شوید!")
        return ConversationHandler.END
    if 'ad' not in context.user_data:
        context.user_data['ad'] = {'photos': []}
    await message.reply_text("📝 لطفاً عنوان آگهی خود را وارد کنید:")
    return AD_TITLE

async def receive_ad_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.effective_message.reply_text("لطفاً عنوان معتبر وارد کنید.")
        return AD_TITLE
    context.user_data['ad']['title'] = title
    await update.effective_message.reply_text("لطفا توضیحات آگهی را وارد کنید:")
    return AD_DESCRIPTION

async def receive_ad_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.effective_message.reply_text("لطفاً توضیحات معتبر وارد کنید.")
        return AD_DESCRIPTION
    context.user_data['ad']['description'] = description
    await update.effective_message.reply_text("لطفا قیمت آگهی را وارد کنید:")
    return AD_PRICE

async def receive_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = update.message.text.strip()
    if not price.replace(",", "").isdigit():
        await update.effective_message.reply_text("لطفاً قیمت را به صورت عددی و به تومان وارد کنید.")
        return AD_PRICE
    context.user_data['ad']['price'] = price
    await update.effective_message.reply_text("لطفا عکس آگهی را ارسال کنید:")
    return AD_PHOTOS

async def receive_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad = context.user_data['ad']
    if update.message.text and update.message.text.lower() == "هیچ":
        ad['photos'] = []
        return await request_phone(update, context)
    elif update.message.photo:
        ad['photos'].append(update.message.photo[-1].file_id)
        await update.effective_message.reply_text("عکس دریافت شد. برای ارسال عکس دیگر، عکس بفرستید یا بنویسید 'تمام' برای اتمام.")
        return AD_PHOTOS
    elif update.message.text and update.message.text.lower() == "تمام" and ad['photos']:
        return await request_phone(update, context)
    else:
        await update.effective_message.reply_text("لطفا یک عکس ارسال کنید یا بنویسید 'هیچ' یا 'تمام'.")
        return AD_PHOTOS

async def request_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        with conn:
            user_data = conn.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if not user_data or not user_data[0]:
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
        else:
            return await save_ad(update, context)
    except sqlite3.Error as e:
        logger.error(f"Database error in request_phone: {e}")
        await update.effective_message.reply_text("❌ خطایی در بررسی اطلاعات رخ داد. لطفاً بعداً دوباره تلاش کنید.")
        return ConversationHandler.END
    finally:
        conn.close()

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = None

    if update.message.contact:
        phone = update.message.contact.phone_number.strip()
    elif update.message.text:
        phone = update.message.text.strip()

    if not phone or not phone.replace("+", "").isdigit() or len(phone.replace("+", "")) < 10:
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📞 ارسال شماره", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.effective_message.reply_text(
            "⚠️ لطفاً یک شماره تلفن معتبر وارد کنید یا از دکمه زیر استفاده کنید:",
            reply_markup=keyboard
        )
        return AD_PHONE

    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('UPDATE users SET phone = ? WHERE user_id = ?', (phone, user_id))
        conn.commit()
        await update.effective_message.reply_text(
            "✅ شماره تلفن با موفقیت ثبت شد. آگهی شما در حال ارسال برای تأیید است...",
            reply_markup=ReplyKeyboardRemove()
        )
        return await save_ad(update, context)
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
    created_at = datetime.now().isoformat()
    photos = ",".join(ad['photos']) if ad['photos'] else ""
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('INSERT INTO ads (user_id, title, description, price, photos, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                  (user_id, ad['title'], ad['description'], ad['price'], photos, created_at))
        conn.commit()
        ad_id = c.lastrowid
        await update.effective_message.reply_text("✅ آگهی با موفقیت ثبت شد و در انتظار تأیید مدیر است.")
        # نوتیفیکیشن به ادمین‌ها
        for admin_id in ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📢 آگهی جدید ثبت شد:\nعنوان: {ad['title']}\nID: {ad_id}\nلطفاً در پنل ادمین بررسی کنید."
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        return ConversationHandler.END
    except sqlite3.Error as e:
        logger.error(f"Database error in save_ad: {e}")
        await update.effective_message.reply_text("خطایی در ثبت آگهی رخ داد. لطفاً دوباره تلاش کنید.")
        return ConversationHandler.END
    finally:
        conn.close()

async def start_edit_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update, context):
        return ConversationHandler.END
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📞 ارسال شماره", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await update.effective_message.reply_text(
        "📞 لطفاً شماره تلفن خود را با زدن دکمه زیر ارسال کنید:",
        reply_markup=keyboard
    )
    return AD_PHONE

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی ممنوع!")
        return

    page = context.user_data.get('admin_page', 1)
    items_per_page = 5
    status_filter = context.user_data.get('admin_status_filter', 'pending')

    conn = get_db_connection()
    try:
        c = conn.cursor()
        total_ads = c.execute('SELECT COUNT(*) FROM ads WHERE status = ?', (status_filter,)).fetchone()[0]
        total_pages = (total_ads + items_per_page - 1) // items_per_page

        if page < 1 or page > total_pages:
            page = 1
            context.user_data['admin_page'] = page

        offset = (page - 1) * items_per_page
        ads = c.execute(
            'SELECT * FROM ads WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (status_filter, items_per_page, offset)
        ).fetchall()

        if not ads:
            await update.effective_message.reply_text(
                f"هیچ آگهی‌ای با وضعیت '{status_filter}' یافت نشد.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 تغییر وضعیت", callback_data="change_status")],
                    [InlineKeyboardButton("🏠 بازگشت", callback_data="admin_exit")]
                ])
            )
            return

        for ad in ads:
            user_info = c.execute('SELECT phone FROM users WHERE user_id = ?', (ad['user_id'],)).fetchone()
            phone = user_info['phone'] if user_info else "نامشخص"
            user = await context.bot.get_chat(ad['user_id'])
            username = user.username or f"{user.first_name} {user.last_name or ''}"

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
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{ad['id']}"),
                 InlineKeyboardButton("❌ رد", callback_data=f"reject_{ad['id']}")],
                [InlineKeyboardButton("🖼️ نمایش تصاویر", callback_data=f"show_photos_{ad['id']}")]
            ]

            if ad['photos']:
                for photo in ad['photos'].split(","):
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=photo,
                        caption=ad_text,
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                    await asyncio.sleep(0.5)
            else:
                await update.effective_message.reply_text(
                    ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            await asyncio.sleep(0.5)

        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ صفحه قبلی", callback_data=f"page_{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("➡️ صفحه بعدی", callback_data=f"page_{page+1}"))
        nav_buttons_row = nav_buttons if nav_buttons else []

        await update.effective_message.reply_text(
            f"📄 صفحه {page} از {total_pages} (وضعیت: {status_filter})",
            reply_markup=InlineKeyboardMarkup([
                nav_buttons_row,
                [InlineKeyboardButton("🔄 تغییر وضعیت", callback_data="change_status")],
                [InlineKeyboardButton("🏠 بازگشت", callback_data="admin_exit")]
            ])
        )

    except Exception as e:
        logger.error(f"Error in admin_panel: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در نمایش آگهی‌ها رخ داد. لطفاً دوباره تلاش کنید."
        )
    finally:
        conn.close()

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی ممنوع!")
        return
    action, ad_id = query.data.split("_")
    conn = get_db_connection()
    try:
        c = conn.cursor()
        ad = c.execute('SELECT user_id, title FROM ads WHERE id = ?', (ad_id,)).fetchone()
        if action == "approve":
            c.execute('UPDATE ads SET status="approved" WHERE id=?', (ad_id,))
            await query.message.reply_text(f"آگهی {ad_id} تأیید شد.")
            # نوتیفیکیشن به کاربر
            try:
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=f"✅ آگهی شما با عنوان '{ad['title']}' تأیید شد و در کانال نمایش داده خواهد شد."
                )
            except Exception as e:
                logger.error(f"Failed to notify user {ad['user_id']}: {e}")
        elif action == "reject":
            c.execute('UPDATE ads SET status="rejected" WHERE id=?', (ad_id,))
            await query.message.reply_text(f"آگهی {ad_id} رد شد.")
            # نوتیفیکیشن به کاربر
            try:
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=f"❌ آگهی شما با عنوان '{ad['title']}' رد شد. لطفاً با ادمین تماس بگیرید."
                )
            except Exception as e:
                logger.error(f"Failed to notify user {ad['user_id']}: {e}")
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error in handle_admin_action: {e}")
        await query.message.reply_text("خطایی در پردازش درخواست رخ داد.")
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
        context.user_data['admin_page'] = int(data.split("_")[1])
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
        context.user_data['admin_status_filter'] = data.split("_")[1]
        context.user_data['admin_page'] = 1
        await admin_panel(update, context)
    elif data.startswith("show_photos_"):
        ad_id = data.split("_")[2]
        conn = get_db_connection()
        try:
            ad = conn.execute('SELECT photos FROM ads WHERE id = ?', (ad_id,)).fetchone()
            if ad and ad['photos']:
                for photo in ad['photos'].split(","):
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=photo,
                        caption=f"تصویر آگهی {ad_id}"
                    )
                    await asyncio.sleep(0.5)
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

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی ممنوع! فقط ادمین‌ها می‌توانند ادمین اضافه کنند.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("لطفاً ID کاربر را به صورت عددی وارد کنید. مثال: /add_admin 123456789")
        return

    new_admin_id = int(args[0])
    if new_admin_id in ADMIN_ID:
        await update.effective_message.reply_text("⚠️ این کاربر قبلاً ادمین است.")
        return

    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('INSERT INTO admins (user_id) VALUES (?)', (new_admin_id,))
        conn.commit()
        ADMIN_ID.append(new_admin_id)  # به‌روزرسانی لیست ادمین‌ها
        await update.effective_message.reply_text(f"✅ کاربر با ID {new_admin_id} به عنوان ادمین اضافه شد.")
        # نوتیفیکیشن به همه ادمین‌ها
        for admin_id in ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📢 ادمین جدید اضافه شد: ID {new_admin_id}"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in add_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در افزودن ادمین رخ داد.")
    finally:
        conn.close()

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی ممنوع! فقط ادمین‌ها می‌توانند ادمین حذف کنند.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("لطفاً ID کاربر را به صورت عددی وارد کنید. مثال: /remove_admin 123456789")
        return

    admin_id_to_remove = int(args[0])
    if admin_id_to_remove not in ADMIN_ID:
        await update.effective_message.reply_text("⚠️ این کاربر ادمین نیست.")
        return

    if len(ADMIN_ID) <= 1:
        await update.effective_message.reply_text("⚠️ نمی‌توانید آخرین ادمین را حذف کنید!")
        return

    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id_to_remove,))
        conn.commit()
        ADMIN_ID.remove(admin_id_to_remove)  # به‌روزرسانی لیست ادمین‌ها
        await update.effective_message.reply_text(f"✅ کاربر با ID {admin_id_to_remove} از لیست ادمین‌ها حذف شد.")
        # نوتیفیکیشن به همه ادمین‌ها
        for admin_id in ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📢 ادمین با ID {admin_id_to_remove} حذف شد."
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in remove_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در حذف ادمین رخ داد.")
    finally:
        conn.close()

async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    one_year_ago = datetime.now() - timedelta(days=365)
    conn = get_db_connection()
    try:
        c = conn.cursor()
        ads = c.execute("SELECT * FROM ads WHERE status='approved' AND datetime(created_at) >= ?", (one_year_ago.isoformat(),)).fetchall()
        if not ads:
            await update.effective_message.reply_text("هیچ آگهی تأیید شده‌ای وجود ندارد.")
            return
        for ad in ads:
            text = f"📌 عنوان: {ad['title']}\n💬 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}"
            try:
                if ad['photos']:
                    for photo in ad['photos'].split(","):
                        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo, caption=text)
                        await asyncio.sleep(0.5)
                else:
                    await update.effective_message.reply_text(text)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to send ad {ad['id']}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in show_ads: {e}")
        await update.effective_message.reply_text("خطایی در نمایش آگهی‌ها رخ داد.")
    finally:
        conn.close()

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی ممنوع!")
        return
    conn = get_db_connection()
    try:
        c = conn.cursor()
        total_users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        total_ads = c.execute('SELECT COUNT(*) FROM ads').fetchone()[0]
        total_admins = c.execute('SELECT COUNT(*) FROM admins').fetchone()[0]
        await update.effective_message.reply_text(
            f"📊 آمار:\nکاربران: {total_users}\nآگهی‌ها: {total_ads}\nادمین‌ها: {total_admins}"
        )
    except sqlite3.Error as e:
        logger.error(f"Database error in stats: {e}")
        await update.effective_message.reply_text("خطایی در نمایش آمار رخ داد.")
    finally:
        conn.close()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.warning(f'Update "{update}" caused error "{context.error}"')

def main():
    try:
        logger.info("Starting bot...")
        application = Application.builder().token(TOKEN).build()

        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("post_ad", post_ad),
                CallbackQueryHandler(post_ad, pattern="post_ad"),
                CommandHandler("edit_info", start_edit_info),
                CallbackQueryHandler(start_edit_info, pattern="edit_info"),
            ],
            states={
                AD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_title)],
                AD_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_description)],
                AD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_price)],
                AD_PHOTOS: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), receive_ad_photos)],
                AD_PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), receive_phone)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            per_message=False
        )

        application.add_handler(CommandHandler("start", start))
        application.add_handler(conv_handler)
        application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(approve|reject|page|status|show_photos|change_status|admin_exit)_"))
        application.add_handler(CommandHandler("stats", stats))
        application.add_handler(CallbackQueryHandler(stats, pattern="stats"))
        application.add_handler(CommandHandler("show_ads", show_ads))
        application.add_handler(CallbackQueryHandler(show_ads, pattern="show_ads"))
        application.add_handler(CallbackQueryHandler(check_membership_callback, pattern="check_membership"))
        application.add_handler(CommandHandler("admin", admin_panel))
        application.add_handler(CommandHandler("add_admin", add_admin))
        application.add_handler(CommandHandler("remove_admin", remove_admin))
        application.add_error_handler(error_handler)

        logger.info("Bot is running...")
        application.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    except Exception as e:
        logger.error("Error in main: %s", str(e))
        raise

if __name__ == "__main__":
    main()
