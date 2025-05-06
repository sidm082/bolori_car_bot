import os
import sqlite3
import logging
import asyncio
import re
import random
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
from telegram.error import TelegramError, RetryAfter
from dotenv import load_dotenv

# تنظیمات لاگ‌گیری
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# بارگذاری متغیرهای محیطی
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    logger.error("BOT_TOKEN not found in .env file")
    raise ValueError("لطفاً توکن ربات را در فایل .env تنظیم کنید.")

# تنظیمات کانال
CHANNEL_URL = "https://t.me/bolori_car"
CHANNEL_ID = "@bolori_car"
CHANNEL_USERNAME = "bolori_car"

# مراحل گفتگو
AD_TITLE, AD_DESCRIPTION, AD_PRICE, AD_PHOTOS, AD_PHONE = range(5)
EDIT_AD, SELECT_AD, EDIT_FIELD = range(3)

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

def update_admin_ids():
    global ADMIN_ID
    ADMIN_ID = load_admin_ids()

# --- تابع پاکسازی متن ---
def clean_text(text):
    if not text:
        return text
    text = re.sub(r'[_*[\]()~`>#+-=|{}.!]', '', text)
    text = ' '.join(text.split())
    return text

# --- تابع کمکی برای مدیریت نرخ ارسال ---
async def send_message_with_rate_limit(bot, chat_id, text=None, photo=None, reply_markup=None, parse_mode=None):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if photo:
                await bot.send_photo(
                    chat_id=chat_id, 
                    photo=photo, 
                    caption=text, 
                    reply_markup=reply_markup, 
                    parse_mode=parse_mode
                )
            else:
                await bot.send_message(
                    chat_id=chat_id, 
                    text=text, 
                    reply_markup=reply_markup, 
                    parse_mode=parse_mode
                )
            await asyncio.sleep(0.5)
            return True
        except RetryAfter as e:
            delay = e.retry_after + random.uniform(0.1, 0.5)
            logger.warning(f"Rate limit hit: retrying after {delay}s")
            await asyncio.sleep(delay)
        except TelegramError as e:
            logger.error(f"Telegram error for chat {chat_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error for chat {chat_id}: {e}")
            return False
    logger.error(f"Failed to send message to {chat_id} after {max_retries} attempts")
    return False

# --- توابع اصلی ربات ---
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    max_retries = 3
    for attempt in range(max_retries):
        try:
            member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            return member.status in ['member', 'administrator', 'creator']
        except TelegramError as e:
            logger.error(f"Attempt {attempt + 1} failed for user {user_id}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
            else:
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
            await query.edit_message_text("✅ عضویت شما تأیید شد! حالا می‌توانید ادامه دهید.")
            await start(update, context)
        else:
            await query.answer("شما هنوز عضو کانال نشده‌اید!", show_alert=True)
    except Exception as e:
        logger.error(f"بررسی عضویت در پاسخ به callback برای کاربر {user_id} ناموفق بود: {e}")
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
            "به ربات رسمی ثبت آگهی خودرو *اتوگالری بلوری* خوش آمدید. از طریق این ربات می‌توانید:\n"
            "  - آگهی فروش خودروی خود را به‌صورت مرحله‌به‌مرحله ثبت کنید\n"
            "  - آگهی‌های ثبت‌شده را مشاهده و جست‌وجو کنید\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:\n\n"
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
            logger.error(f"خطای پایگاه داده در start: {e}")
            await update.effective_message.reply_text("❌ خطایی در ثبت اطلاعات شما در سیستم رخ داد.")
        finally:
            conn.close()

async def start_edit_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    message = query.message
    
    if not await check_membership(update, context):
        await message.reply_text("⚠️ لطفاً ابتدا در کانال عضو شوید!")
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
        logger.error(f"خطای پایگاه داده در start_edit_info: {e}")
        await message.reply_text("❌ خطایی در بررسی اطلاعات رخ داد.")
        return ConversationHandler.END
    finally:
        conn.close()

async def start_edit_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        ads = conn.execute(
            'SELECT id, title FROM ads WHERE user_id = ? AND status = "pending"',
            (user_id,)
        ).fetchall()
        
        if not ads:
            await query.message.reply_text("شما هیچ آگهی در انتظار تأییدی ندارید.")
            return ConversationHandler.END
        
        buttons = [
            [InlineKeyboardButton(f"{ad['title']} (ID: {ad['id']})", callback_data=f"edit_ad_{ad['id']}")]
            for ad in ads
        ]
        buttons.append([InlineKeyboardButton("لغو", callback_data="cancel_edit")])
        
        await query.message.reply_text(
            "لطفاً آگهی مورد نظر برای ویرایش را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return SELECT_AD
    finally:
        conn.close()

async def select_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    ad_id = int(query.data.split('_')[2])
    context.user_data['edit_ad_id'] = ad_id
    
    buttons = [
        [InlineKeyboardButton("عنوان", callback_data="edit_title")],
        [InlineKeyboardButton("توضیحات", callback_data="edit_description")],
        [InlineKeyboardButton("قیمت", callback_data="edit_price")],
        [InlineKeyboardButton("عکس‌ها", callback_data="edit_photos")],
        [InlineKeyboardButton("لغو", callback_data="cancel_edit")]
    ]
    
    await query.message.reply_text(
        "کدام بخش از آگهی را می‌خواهید ویرایش کنید؟",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EDIT_FIELD

async def edit_ad_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    field = query.data
    ad_id = context.user_data['edit_ad_id']
    
    if field == "edit_title":
        await query.message.reply_text("لطفاً عنوان جدید را وارد کنید:")
        context.user_data['edit_field'] = 'title'
    elif field == "edit_description":
        await query.message.reply_text("لطفاً توضیحات جدید را وارد کنید:")
        context.user_data['edit_field'] = 'description'
    elif field == "edit_price":
        await query.message.reply_text("لطفاً قیمت جدید را به تومان وارد کنید:")
        context.user_data['edit_field'] = 'price'
    elif field == "edit_photos":
        context.user_data['ad'] = {'photos': []}
        await query.message.reply_text(
            "لطفاً عکس‌های جدید را ارسال کنید (حداکثر ۵ تصویر) یا 'تمام' یا 'هیچ' را بنویسید:"
        )
        context.user_data['edit_field'] = 'photos'
        return AD_PHOTOS
    
    return EDIT_FIELD

async def receive_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    ad_id = context.user_data['edit_ad_id']
    field = context.user_data['edit_field']
    
    if field == 'photos':
        ad = context.user_data['ad']
        if update.message.text and update.message.text.lower() == "هیچ":
            ad['photos'] = []
            return await save_edited_ad(update, context)
        elif update.message.photo:
            if len(ad['photos']) >= 5:
                await update.effective_message.reply_text(
                    "⚠️ شما حداکثر ۵ تصویر می‌توانید ارسال کنید. لطفاً 'تمام' را بنویسید."
                )
                return AD_PHOTOS
            ad['photos'].append(update.message.photo[-1].file_id)
            await update.effective_message.reply_text(
                f"عکس دریافت شد ({len(ad['photos'])}/۵). برای ارسال عکس دیگر، عکس بفرستید یا 'تمام' را ارسال کنید."
            )
            return AD_PHOTOS
        elif update.message.text and update.message.text.lower() == "تمام":
            if not ad['photos']:
                await update.effective_message.reply_text("لطفاً حداقل یک عکس ارسال کنید یا 'هیچ' را بفرستید.")
                return AD_PHOTOS
            return await save_edited_ad(update, context)
        else:
            await update.effective_message.reply_text(
                "لطفاً یک عکس ارسال کنید یا 'تمام' یا 'هیچ' را بنویسید."
            )
            return AD_PHOTOS
    
    value = update.message.text.strip()
    if not value:
        await update.effective_message.reply_text("لطفاً مقدار معتبر وارد کنید.")
        return EDIT_FIELD
    
    if field == 'price':
        try:
            price_int = int(value.replace(",", ""))
            if price_int <= 0:
                raise ValueError
            value = f"{price_int:,}"
        except ValueError:
            await update.effective_message.reply_text("لطفاً قیمت را به صورت عددی و به تومان وارد کنید.")
            return EDIT_FIELD
    
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                f'UPDATE ads SET {field} = ? WHERE id = ?',
                (value, ad_id)
            )
        await update.effective_message.reply_text("✅ آگهی با موفقیت ویرایش شد.")
        return await save_edited_ad(update, context)
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در receive_edit_field: {e}")
        await update.effective_message.reply_text("❌ خطایی در ویرایش آگهی رخ داد.")
        return ConversationHandler.END
    finally:
        conn.close()

async def save_edited_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad_id = context.user_data['edit_ad_id']
    if 'ad' in context.user_data and context.user_data['ad'].get('photos') is not None:
        photos = context.user_data['ad']['photos']
        conn = get_db_connection()
        try:
            with conn:
                conn.execute(
                    'UPDATE ads SET photos = ? WHERE id = ?',
                    (','.join(photos) if photos else '', ad_id)
                )
        finally:
            conn.close()
    
    await update.effective_message.reply_text(
        "✅ آگهی با موفقیت ویرایش شد و در انتظار تأیید مدیر است."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    message = query.message
    
    if not await check_membership(update, context):
        await message.reply_text("⚠️ لطفاً ابتدا در کانال عضو شوید!")
        return ConversationHandler.END
    
    context.user_data['ad'] = {'photos': []}
    await message.reply_text("📝 لطفاً برند و مدل خودروی خود را وارد کنید (مثال: پژو ۲۰۶ تیپ ۲، کیا سراتو، تویوتا کمری و ...):")
    return AD_TITLE

async def receive_ad_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    if not update.message.text:
        await update.effective_message.reply_text("لطفاً فقط متن وارد کنید.")
        return AD_TITLE
    
    title = update.message.text.strip()
    if len(title) > 100:
        await update.effective_message.reply_text("عنوان بیش از حد طولانی است (حداکثر ۱۰۰ کاراکتر).")
        return AD_TITLE
    
    context.user_data['ad']['title'] = title
    await update.effective_message.reply_text("لطفاً اطلاعات خودرو شامل رنگ، کارکرد، وضعیت بدنه، وضعیت فنی و غیره را وارد کنید.")
    return AD_DESCRIPTION

async def receive_ad_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    if not update.message.text:
        await update.effective_message.reply_text("لطفاً فقط متن وارد کنید.")
        return AD_DESCRIPTION
    
    description = update.message.text.strip()
    if len(description) > 1000:
        await update.effective_message.reply_text("توضیحات بیش از حد طولانی است (حداکثر ۱۰۰۰ کاراکتر).")
        return AD_DESCRIPTION
    
    context.user_data['ad']['description'] = description
    await update.effective_message.reply_text("لطفاً قیمت خودرو را به تومان وارد کنید:")
    return AD_PRICE

async def receive_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    price = update.message.text.strip().replace(",", "")
    try:
        price_int = int(price)
        if price_int <= 0:
            raise ValueError("قیمت باید مثبت باشد")
        context.user_data['ad']['price'] = f"{price_int:,}"
        await update.effective_message.reply_text(
            "لطفاً عکس خودرو را ارسال کنید (حداکثر ۵ تصویر) (یا 'تمام' برای اتمام یا 'هیچ' اگر عکسی ندارید):"
        )
        return AD_PHOTOS
    except ValueError:
        await update.effective_message.reply_text("لطفاً قیمت را به صورت عددی و به تومان وارد کنید (مثال: 500000000).")
        return AD_PRICE

async def receive_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    ad = context.user_data['ad']
    
    if update.message.text and update.message.text.lower() == "هیچ":
        ad['photos'] = []
        return await request_phone(update, context)
    elif update.message.photo:
        if len(ad['photos']) >= 5:
            await update.effective_message.reply_text(
                "⚠️ شما حداکثر ۵ تصویر می‌توانید ارسال کنید. لطفاً 'تمام' را بنویسید."
            )
            return AD_PHOTOS
        ad['photos'].append(update.message.photo[-1].file_id)
        await update.effective_message.reply_text(
            f"عکس دریافت شد ({len(ad['photos'])}/۵). برای ارسال عکس دیگر، عکس بفرستید یا 'تمام' را ارسال کنید."
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
    query = update.callback_query
    if query:
        await query.answer()
    
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
        logger.error(f"خطای پایگاه داده در request_phone: {e}")
        await update.effective_message.reply_text("❌ خطایی در بررسی اطلاعات رخ داد.")
        return ConversationHandler.END
    finally:
        conn.close()

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
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
        
        if 'ad' not in context.user_data or not context.user_data['ad']:
            await update.effective_message.reply_text(
                "✅ شماره تلفن با موفقیت به‌روزرسانی شد.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        ad = context.user_data['ad']
        required_fields = ['title', 'description', 'price']
        missing_fields = [field for field in required_fields if field not in ad]
        
        if missing_fields:
            logger.warning(f"Missing fields in ad data for user {user_id}: {missing_fields}")
            await update.effective_message.reply_text(
                "⚠️ لطفاً ابتدا اطلاعات آگهی (عنوان، توضیحات، قیمت) را وارد کنید. از منوی اصلی گزینه 'ثبت آگهی' را انتخاب کنید.",
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END
        
        context.user_data['ad']['phone'] = cleaned_phone
        await update.effective_message.reply_text(
            "✅ شماره تلفن با موفقیت ثبت شد. آگهی شما در حال ارسال برای تأیید است...",
            reply_markup=ReplyKeyboardRemove()
        )
        return await save_ad(update, context)
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در receive_phone: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در ثبت اطلاعات رخ داد. لطفاً دوباره تلاش کنید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return AD_PHONE
    finally:
        conn.close()

async def save_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
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
                    text=f"📢 آگهی جدید ثبت شد:\nعنوان: {clean_text(ad['title'])}\nشناسه: {ad_id}\nلطفاً در پنل مدیریت بررسی کنید.",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"خطا در اطلاع‌رسانی به ادمین {admin_id}: {e}")
        
        await update.effective_message.reply_text(
            "با تشکر از اعتماد شما. ✅ آگهی با موفقیت ثبت شد و در انتظار تأیید مدیر است.\n"
            "می‌توانید از منوی اصلی برای ثبت آگهی جدید ادامه دهید."
        )
        context.user_data.clear()
        return ConversationHandler.END
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در save_ad: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در ثبت آگهی رخ داد، لطفاً دوباره تلاش کنید."
        )
        return ConversationHandler.END
    finally:
        conn.close()

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    message = query.message
    
    if update.effective_user.id not in ADMIN_ID:
        await message.reply_text("❌ دسترسی غیرمجاز!")
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
                text=f"هیچ آگهی با وضعیت '{status_filter}' یافت نشد.",
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
            
            phone = user_info['phone'] if user_info else "ناشناس"
            
            try:
                user = await context.bot.get_chat(ad['user_id'])
                username = user.username or f"{user.first_name} {user.last_name or ''}"
            except Exception:
                user = None
                username = "ناشناس"
            
            ad_text = (
                f"🆔 آگهی: {ad['id']}\n"
                f"👤 کاربر: {username}\n"
                f"📞 شماره: {clean_text(phone)}\n"
                f"📌 عنوان: {clean_text(ad['title'])}\n"
                f"💬 توضیحات: {clean_text(ad['description'])}\n"
                f"💰 قیمت: {clean_text(ad['price'])}\n"
                f"📅 تاریخ: {ad['created_at']}\n"
                f"📸 تصاویر: {'دارد' if ad['photos'] else 'ندارد'}\n"
                f"📊 وضعیت: {ad['status']}"
            )
            
            buttons = [
                [
                    InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{ad['id']}"),
                    InlineKeyboardButton("❌ رد", callback_data=f"reject_{ad['id']}")
                ],
                [InlineKeyboardButton("🖼️ مشاهده تصاویر", callback_data=f"show_photos_{ad['id']}")]
            ]
            
            if ad['photos']:
                photos = ad['photos'].split(',')
                await send_message_with_rate_limit(
                    context.bot,
                    update.effective_chat.id,
                    text=ad_text,
                    photo=photos[0],
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode='Markdown'
                )
            else:
                await send_message_with_rate_limit(
                    context.bot,
                    update.effective_chat.id,
                    text=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode='Markdown'
                )
        
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ صفحه قبل", callback_data=f"page_{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("➡️ صفحه بعد", callback_data=f"page_{page+1}"))
        
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
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"خطا در پنل مدیریت: {e}")
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
        await query.message.reply_text("❌ دسترسی غیرمجاز!")
        return
    
    action, ad_id = query.data.split('_')
    ad_id = int(ad_id)
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        ad = cursor.execute(
            'SELECT user_id, title, description, price, photos, status, created_at FROM ads WHERE id = ?', 
            (ad_id,)
        ).fetchone()
        
        if not ad:
            await query.message.reply_text("❌ آگهی یافت نشد!")
            return
        
        if action == "approve":
            new_status = "approved"
            user_message = f"✅ آگهی شما *{clean_text(ad['title'])}* تأیید شد و در کانال منتشر شد."
            
            user_info = cursor.execute(
                'SELECT phone FROM users WHERE user_id = ?', 
                (ad['user_id'],)
            ).fetchone()
            phone = clean_text(user_info['phone'] if user_info else "ناشناس")
            
            title = clean_text(ad['title'])
            description = clean_text(ad['description'])
            price = clean_text(ad['price'])
            
            ad_text = (
                f"🚗 {title}\n\n"
                f"📝 توضیحات: {description}\n"
                f"💰 قیمت: {price} تومان\n"
                f"📞 تماس: {phone}\n\n"
                f"➖➖➖➖➖\n"
                f"☑️ اتوگالری بلوری\n"
                f"▫️خرید▫️فروش▫️کارشناسی\n"
                f"📲 +989153632957\n"
                f"📍 @{CHANNEL_USERNAME}"
            )
            
            try:
                if ad['photos']:
                    photos = ad['photos'].split(',')
                    await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=photos[0],
                        caption=ad_text
                    )
                    for photo in photos[1:3]:
                        await context.bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=photo
                        )
                else:
                    await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=ad_text
                    )
            except TelegramError as e:
                logger.error(f"خطا در ارسال آگهی به کانال: {e}")
                await query.message.reply_text("❌ خطایی در انتشار آگهی در کانال رخ داد.")
                return
            
            cursor.execute(
                'UPDATE ads SET status = ? WHERE id = ?',
                (new_status, ad_id)
            )
            conn.commit()
            
            try:
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=user_message,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"خطا در اطلاع‌رسانی به کاربر {ad['user_id']}: {e}")
            
            await query.message.reply_text(f"✅ آگهی {ad_id} تأیید و در کانال منتشر شد.")
        
        elif action == "reject":
            new_status = "rejected"
            user_message = f"❌ آگهی شما *{clean_text(ad['title'])}* رد شد."
            
            cursor.execute(
                'UPDATE ads SET status = ? WHERE id = ?',
                (new_status, ad_id)
            )
            conn.commit()
            
            try:
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=user_message,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"خطا در اطلاع‌رسانی به کاربر {ad['user_id']}: {e}")
            
            await query.message.reply_text(f"❌ آگهی {ad_id} رد شد.")
        
        await admin_panel(update, context)
    
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در handle_admin_action: {e}")
        await query.message.reply_text("❌ خطایی در پردازش درخواست رخ داد.")
    finally:
        conn.close()

async def change_status_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی غیرمجاز!")
        return
    
    data = query.data
    if data == "change_status":
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

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی غیرمجاز!")
        return
    
    data = query.data
    
    if data.startswith("approve_") or data.startswith("reject_"):
        await handle_admin_action(update, context)
    elif data.startswith("page_"):
        context.user_data['admin_page'] = int(data.split('_')[1])
        await admin_panel(update, context)
    elif data == "change_status" or data.startswith("status_"):
        await change_status_filter(update, context)
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
                        text=f"تصاویر آگهی {ad_id}",
                        photo=photo
                    )
            else:
                await query.message.reply_text("📸 این آگهی هیچ تصویری ندارد.")
        except Exception as e:
            logger.error(f"خطا در نمایش تصاویر: {e}")
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
    await query.answer()
    message = query.message
    
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
                text="هیچ آگهی تأیید شده‌ای یافت نشد."
            )
            return
        
        for ad in ads:
            user_info = cursor.execute(
                'SELECT phone FROM users WHERE user_id = ?', 
                (ad['user_id'],)
            ).fetchone()
            phone = clean_text(user_info['phone'] if user_info else "ناشناس")
            
            text = (
                f"📌 *عنوان*: {clean_text(ad['title'])}\n"
                f"💬 *توضیحات*: {clean_text(ad['description'])}\n"
                f"💰 *قیمت*: {clean_text(ad['price'])} تومان\n"
                f"📞 *شماره تماس*: {phone}\n"
                f"📅 *تاریخ*: {ad['created_at']}"
            )
            
            try:
                if ad['photos']:
                    photos = ad['photos'].split(',')
                    for photo in photos[:3]:
                        await send_message_with_rate_limit(
                            context.bot,
                            update.effective_chat.id,
                            text=text,
                            photo=photo,
                            parse_mode='Markdown'
                        )
                else:
                    await send_message_with_rate_limit(
                        context.bot,
                        update.effective_chat.id,
                        text=text,
                        parse_mode='Markdown'
                    )
            except Exception as e:
                logger.error(f"ارسال آگهی {ad['id']} ناموفق بود: {e}")
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در show_ads: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آگهی‌ها رخ داد."
        )
    finally:
        conn.close()

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    message = query.message
    
    if update.effective_user.id not in ADMIN_ID:
        await message.reply_text("❌ دسترسی غیرمجاز!")
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
            f"👥 تعداد کل کاربران: {total_users}\n"
            f"🆕 کاربران جدید امروز: {new_users_today}\n\n"
            f"📝 تعداد کل آگهی‌ها: {total_ads}\n"
            f"⏳ در انتظار تأیید: {pending_ads}\n"
            f"✅ تأیید شده: {approved_ads}\n\n"
            f"👨‍💼 تعداد مدیران: {total_admins}"
        )
        
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text=stats_text,
            parse_mode='Markdown'
        )
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در stats: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آمار رخ داد."
        )
    finally:
        conn.close()

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی غیرمجاز!")
        return
    
    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(
            "⚠️ لطفاً شناسه کاربر را وارد کنید:\n"
            "مثال: /add_admin 123456789"
        )
        return
    
    new_admin_id = int(args[0])
    if new_admin_id in ADMIN_ID:
        await update.effective_message.reply_text("⚠️ این کاربر قبلاً مدیر است.")
        return
    
    conn = get_db_connection()
    try:
        with conn:
            conn.execute('INSERT INTO admins (user_id) VALUES (?)', (new_admin_id,))
        update_admin_ids()
        await update.effective_message.reply_text(f"✅ کاربر با شناسه {new_admin_id} به عنوان مدیر اضافه شد.")
        
        try:
            await send_message_with_rate_limit(
                context.bot,
                new_admin_id,
                text=f"🎉 شما به عنوان مدیر ربات اتوگالری بلوری منصوب شدید!\n"
                     f"برای دسترسی به پنل مدیریت از دستور /admin استفاده کنید.",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"اطلاع‌رسانی به مدیر جدید {new_admin_id} ناموفق بود: {e}")
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در add_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در افزودن مدیر رخ داد.")
    finally:
        conn.close()

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    if update.effective_user.id not in ADMIN_ID:
        await update.effective_message.reply_text("❌ دسترسی غیرمجاز!")
        return
    
    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(
            "⚠️ لطفاً شناسه کاربر را وارد کنید:\n"
            "مثال: /remove_admin 123456789"
        )
        return
    
    admin_id_to_remove = int(args[0])
    if admin_id_to_remove not in ADMIN_ID:
        await update.effective_message.reply_text("⚠️ این کاربر مدیر نیست.")
        return
    
    if admin_id_to_remove == update.effective_user.id:
        await update.effective_message.reply_text("⚠️ شما نمی‌توانید خودتان را از مدیریت حذف کنید!")
        return
    
    if len(ADMIN_ID) <= 1:
        await update.effective_message.reply_text("⚠️ نمی‌توان آخرین مدیر را حذف کرد!")
        return
    
    conn = get_db_connection()
    try:
        with conn:
            conn.execute('DELETE FROM admins WHERE user_id = ?', (admin_id_to_remove,))
        update_admin_ids()
        await update.effective_message.reply_text(f"✅ کاربر با شناسه {admin_id_to_remove} از لیست مدیران حذف شد.")
        
        try:
            await send_message_with_rate_limit(
                context.bot,
                admin_id_to_remove,
                text="❌ دسترسی مدیریت شما برای ربات اتوگالری بلوری لغو شد.",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"اطلاع‌رسانی به مدیر حذف‌شده {admin_id_to_remove} ناموفق بود: {e}")
    except sqlite3.Error as e:
        logger.error(f"خطای پایگاه داده در remove_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در حذف مدیر رخ داد.")
    finally:
        conn.close()

async def admin_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("page_"):
        context.user_data['admin_page'] = int(query.data.split('_')[1])
        await admin_panel(update, context)
    elif query.data == "admin_exit":
        await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    context.user_data.clear()
    await update.effective_message.reply_text(
        "❌ عملیات فعلی لغو شد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"خطا در پردازش به‌روزرسانی {update}: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید."
            )
        except Exception:
            pass

async def show_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("show_photos_"):
        logger.error(f"Invalid callback data: {query.data}")
        await query.message.reply_text("❌ خطای ناشناخته در پردازش درخواست.")
        return
    
    try:
        ad_id = int(query.data.split('_')[2])
    except (IndexError, ValueError) as e:
        logger.error(f"Invalid ad_id in callback data: {query.data}, error: {e}")
        await query.message.reply_text("❌ خطای ناشناخته در پردازش درخواست.")
        return
    
    conn = get_db_connection()
    try:
        ad = conn.execute(
            'SELECT photos FROM ads WHERE id = ?', 
            (ad_id,)
        ).fetchone()
        
        if not ad or not ad['photos']:
            await query.message.reply_text("این آگهی عکسی ندارد!")
            return
        
        photos = ad['photos'].split(',')
        for photo in photos[:5]:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo
            )
    except Exception as e:
        logger.error(f"خطا در نمایش تصاویر آگهی {ad_id}: {e}")
        await query.message.reply_text("❌ خطایی در نمایش تصاویر رخ داد.")
    finally:
        conn.close()

# --- تنظیمات اصلی ربات ---
async def main():
    # مقداردهی اولیه پایگاه داده
    init_db()
    global ADMIN_ID
    ADMIN_ID = load_admin_ids()
    
    # ایجاد برنامه ربات
    application = Application.builder().token(TOKEN).build()
    
    # اطمینان از حذف وب‌هوک قبلی
    await application.bot.delete_webhook()
    logger.info("🤖 وب‌هوک حذف شد، استفاده از Polling")
    
    # تنظیم هندلر گفت‌وگو برای ثبت آگهی
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(post_ad, pattern="^post_ad$"),
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
        fallbacks=[CallbackQueryHandler(cancel, pattern="^cancel$")],
        per_message=True
    )
    
    # تنظیم هندلر گفت‌وگو برای ویرایش آگهی
    edit_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit_ad, pattern="^edit_info$")],
        states={
            SELECT_AD: [CallbackQueryHandler(select_ad, pattern="^edit_ad_")],
            EDIT_FIELD: [
                CallbackQueryHandler(edit_ad_field, pattern="^edit_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_field),
                MessageHandler(filters.PHOTO, receive_ad_photos)
            ],
            AD_PHOTOS: [
                MessageHandler(filters.PHOTO, receive_ad_photos),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_photos)
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern="^cancel_edit$")],
        per_message=True
    )
    
    # افزودن هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(edit_conv_handler)
    application.add_handler(CallbackQueryHandler(check_membership_callback, pattern="^check_membership$"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(approve_|reject_|page_|change_status|status_|show_photos_|admin_exit)"))
    application.add_handler(CallbackQueryHandler(show_ads, pattern="^show_ads$"))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("add_admin", add_admin))
    application.add_handler(CommandHandler("remove_admin", remove_admin))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), receive_phone))
    application.add_handler(CallbackQueryHandler(show_ad_photos, pattern="^show_photos_"))
    
    # افزودن هندلر خطا
    application.add_error_handler(error_handler)
    
    # راه‌اندازی Polling
    await application.run_polling(
        poll_interval=1.0,
        timeout=10,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # اجرای تابع main در حلقه رویداد موجود
        asyncio.run_coroutine_threadsafe(main(), loop)
    else:
        # اجرای عادی اگر حلقه رویداد در حال اجرا نباشد
        loop.run_until_complete(main())
