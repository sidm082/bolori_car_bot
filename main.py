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
from telegram.error import TelegramError, RetryAfter, BadRequest
from dotenv import load_dotenv
from flask import Flask, request, Response
import nest_asyncio
from contextlib import contextmanager
from queue import Queue
import time

# اعمال nest_asyncio
nest_asyncio.apply()

# تنظیمات لاگ‌گیری
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger('telegram').setLevel(logging.DEBUG)

# بارگذاری متغیرهای محیطی
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
PORT = int(os.getenv('PORT', 8080))

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

# ایجاد اپلیکیشن Flask
flask_app = Flask(__name__)

# متغیر جهانی برای اپلیکیشن تلگرام
_application = None

# صف برای پردازش به‌روزرسانی‌ها
update_queue = Queue()

# لیست ادمین‌ها
ADMIN_ID = []

# ==================== توابع کمکی ====================

def clean_text(text):
    if not text:
        return "نامشخص"
    return re.sub(r'[\*_`\[\\]', '', str(text))

async def send_message_with_rate_limit(bot, chat_id, text=None, photo=None, reply_markup=None, parse_mode=None):
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
    except RetryAfter as e:
        logger.warning(f"Rate limit hit, retrying after {e.retry_after} seconds")
        await asyncio.sleep(e.retry_after)
        await send_message_with_rate_limit(bot, chat_id, text, photo, reply_markup, parse_mode)
    except TelegramError as e:
        logger.error(f"Error sending message to {chat_id}: {e}")

def load_admin_ids():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            admins = cursor.execute('SELECT user_id FROM admins').fetchall()
            logger.info(f"Loaded {len(admins)} admin IDs")
            return [admin['user_id'] for admin in admins]
    except sqlite3.Error as e:
        logger.error(f"Error loading admin IDs: {e}")
        return [5677216420]

def update_admin_ids():
    global ADMIN_ID
    ADMIN_ID = load_admin_ids()
    logger.info(f"Updated ADMIN_ID: {ADMIN_ID}")

# ==================== توابع دسترسی به اپلیکیشن تلگرام ====================

def get_application():
    global _application
    if _application is None:
        logger.info("Initializing Telegram application...")
        loop = asyncio.get_event_loop()
        try:
            _application = Application.builder().token(TOKEN).build()
            logger.info("Telegram application built.")
            _application.add_handler(CommandHandler("start", start))
            _application.add_handler(ConversationHandler(
                entry_points=[
                    CallbackQueryHandler(post_ad, pattern="^post_ad$"),
                    CallbackQueryHandler(post_referral, pattern="^post_referral$"),
                    CommandHandler("post_ad", post_ad)
                ],
                states={
                    AD_TITLE: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_title)
                    ],
                    AD_DESCRIPTION: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_description)
                    ],
                    AD_PRICE: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_price)
                    ],
                    AD_PHOTOS: [
                        MessageHandler(filters.PHOTO, receive_ad_photos),
                        MessageHandler(filters.Regex('^(تمام|هیچ)$'), receive_ad_photos)
                    ],
                    AD_PHONE: [
                        MessageHandler(filters.CONTACT, receive_phone),
                        MessageHandler(filters.Regex(r'^(\+98|0)?9\d{9}$'), receive_phone)
                    ],
                },
                fallbacks=[
                    CommandHandler("cancel", cancel),
                    MessageHandler(filters.COMMAND, cancel)
                ],
                per_message=True
            ))
            _application.add_handler(CallbackQueryHandler(check_membership_callback, pattern="^check_membership$"))
            _application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
            _application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(approve_|reject_|page_|change_status|status_|show_photos_|admin_exit)"))
            _application.add_handler(CallbackQueryHandler(show_ads, pattern="^show_ads$"))
            _application.add_handler(CommandHandler("admin", admin_panel))
            _application.add_handler(CommandHandler("add_admin", add_admin))
            _application.add_handler(CommandHandler("remove_admin", remove_admin))
            _application.add_handler(CommandHandler("stats", stats))
            _application.add_handler(CallbackQueryHandler(show_ad_photos, pattern="^show_photos_"))
            _application.add_error_handler(error_handler)
            logger.info("Handlers registered.")
            loop.run_until_complete(_application.initialize())
            logger.info("Application initialized.")
            loop.run_until_complete(_application.start())
            logger.info("Application started.")
            if WEBHOOK_URL:
                try:
                    loop.run_until_complete(_application.bot.set_webhook(
                        url=WEBHOOK_URL,
                        secret_token=WEBHOOK_SECRET
                    ))
                    logger.info("Webhook set successfully.")
                except TelegramError as e:
                    logger.error(f"Failed to set webhook: {e}")
                    raise
        except Exception as e:
            logger.error(f"Error initializing application: {e}", exc_info=True)
            raise
    return _application

# ==================== بهبودهای Webhook ====================

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    start_time = time.time()
    if WEBHOOK_SECRET and request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
        logger.warning("Invalid webhook secret token")
        return Response('Unauthorized', 401)
    try:
        json_data = request.get_json()
        if not json_data:
            logger.error("Empty webhook data received")
            return Response('Bad Request', 400)
        update_queue.put(json_data)
        logger.info(f"Webhook update queued in {time.time() - start_time:.2f} seconds")
        return Response('', 200)
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return Response('Internal Server Error', 500)

async def process_update_queue():
    logger.info("Starting update queue processing...")
    application = get_application()
    while True:
        try:
            json_data = update_queue.get_nowait()
            start_time = time.time()
            update = Update.de_json(json_data, application.bot)
            if update:
                await application.process_update(update)
                logger.info(f"Processed update in {time.time() - start_time:.2f} seconds")
            else:
                logger.warning("Received invalid update data")
            update_queue.task_done()
        except Queue.Empty:
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error processing queued update: {e}", exc_info=True)

# ==================== بهبودهای دیتابیس ====================

@contextmanager
def get_db_connection():
    logger.info("Opening database connection...")
    conn = sqlite3.connect(
        '/opt/render/project/src/bot.db',
        check_same_thread=False,
        timeout=30,
        isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        raise
    finally:
        conn.close()
        logger.info("Database connection closed.")

def init_db():
    logger.info("Initializing database...")
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS users
                        (user_id INTEGER PRIMARY KEY, 
                         joined TEXT, 
                         phone TEXT)''')
            logger.info("Users table created.")
            c.execute('''CREATE TABLE IF NOT EXISTS ads
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         user_id INTEGER,
                         title TEXT,
                         description TEXT,
                         price TEXT,
                         photos TEXT,
                         created_at TEXT,
                         status TEXT DEFAULT 'pending',
                         is_referral INTEGER,
                         FOREIGN KEY (user_id) REFERENCES users(user_id))''')
            logger.info("Ads table created.")
            c.execute('''CREATE TABLE IF NOT EXISTS admins
                        (user_id INTEGER PRIMARY KEY)''')
            logger.info("Admins table created.")
            c.execute('CREATE INDEX IF NOT EXISTS idx_ads_user_status ON ads(user_id, status)')
            logger.info("Index created.")
            initial_admin_id = 5677216420
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (initial_admin_id,))
            conn.commit()
            logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database error in init_db: {e}")
        raise

# ==================== توابع ربات ====================

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
    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Failed to answer callback query: {e}")
        await query.message.reply_text("لطفاً دوباره تلاش کنید.")
        return
    user_id = query.from_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            await query.edit_message_text("✅ عضویت شما تأیید شد! حالا می‌توانید ادامه دهید.")
            await start(update, context)
        else:
            await query.answer("شما هنوز عضو کانال نشده‌اید!", show_alert=True)
    except Exception as e:
        logger.error(f"بررسی عضویت در callback برای کاربر {user_id} ناموفق بود: {e}")
        await query.answer("خطا در بررسی عضویت. لطفاً دوباره تلاش کنید.", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Start command received from user {update.effective_user.id}")
    user = update.effective_user
    if await check_membership(update, context):
        buttons = [
            [InlineKeyboardButton("➕ ثبت آگهی", callback_data="post_ad")],
            [InlineKeyboardButton("📜 ثبت حواله", callback_data="post_referral")],
            [InlineKeyboardButton("🗂️ نمایش آگهی‌ها", callback_data="show_ads")]
        ]
        if user.id in ADMIN_ID:
            buttons.append([InlineKeyboardButton("👨‍💼 پنل ادمین", callback_data="admin_panel")])
            buttons.append([InlineKeyboardButton("📊 آمار کاربران", callback_data="stats")])
        welcome_text = (
            f"سلام {user.first_name} عزیز! 👋\n\n"
            "به ربات رسمی ثبت آگهی خودرو *اتوگالری بلوری* خوش آمدید. از طریق این ربات می‌توانید:\n"
            "  - آگهی فروش خودروی خود را به‌صورت مرحله‌به‌مرحله ثبت کنید\n"
            "  - حواله خودرو ثبت کنید\n"
            "  - آگهی‌های ثبت‌شده را مشاهده و جست‌وجو کنید\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:\n\n"
        )
        await update.effective_message.reply_text(
            welcome_text, reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        try:
            with get_db_connection() as conn:
                conn.execute(
                    'INSERT OR REPLACE INTO users (user_id, joined) VALUES (?, ?)',
                    (user.id, datetime.now().isoformat())
                )
                conn.commit()
                logger.info(f"User {user.id} registered in database")
        except sqlite3.Error as e:
            logger.error(f"Database error in start: {e}")
            await update.effective_message.reply_text("❌ خطایی در ثبت اطلاعات رخ داد.")

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except BadRequest as e:
            logger.warning(f"Failed to answer callback query: {e}")
            await query.message.reply_text("لطفاً دوباره تلاش کنید.")
            return ConversationHandler.END
    message = update.effective_message
    if not await check_membership(update, context):
        await message.reply_text("⚠️ لطفاً ابتدا در کانال عضو شوید!")
        return ConversationHandler.END
    context.user_data['ad'] = {'photos': [], 'is_referral': 0}
    await message.reply_text("📝 لطفاً برند و مدل خودروی خود را وارد کنید (مثال: پژو ۲۰۶ تیپ ۲، کیا سراتو، تویوتا کمری و ...):")
    logger.info(f"User {update.effective_user.id} started posting ad")
    return AD_TITLE

async def post_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except BadRequest as e:
            logger.warning(f"Failed to answer callback query: {e}")
            await query.message.reply_text("لطفاً دوباره تلاش کنید.")
            return ConversationHandler.END
    message = update.effective_message
    if not await check_membership(update, context):
        await message.reply_text("⚠️ لطفاً ابتدا در کانال عضو شوید!")
        return ConversationHandler.END
    context.user_data['ad'] = {'photos': [], 'is_referral': 1}
    await message.reply_text("📜 لطفاً برند و مدل خودروی حواله را وارد کنید (مثال: پژو ۲۰۶ تیپ ۲، کیا سراتو، تویوتا کمری و ...):")
    logger.info(f"User {update.effective_user.id} started posting referral")
    return AD_TITLE

async def receive_ad_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        await update.effective_message.reply_text("لطفاً فقط متن وارد کنید.")
        return AD_TITLE
    title = update.message.text.strip()
    if len(title) > 100:
        await update.effective_message.reply_text("عنوان بیش از حد طولانی است (حداکثر ۱۰۰ کاراکتر).")
        return AD_TITLE
    context.user_data['ad']['temp_title'] = title
    await update.effective_message.reply_text("لطفاً اطلاعات خودرو یا حواله شامل جزئیات (مثل رنگ، کارکرد، وضعیت بدنه، وضعیت فنی یا شرایط حواله) را وارد کنید.")
    logger.info(f"User {update.effective_user.id} submitted ad title: {title}")
    return AD_DESCRIPTION

async def receive_ad_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        await update.effective_message.reply_text("لطفاً فقط متن وارد کنید.")
        return AD_DESCRIPTION
    description = update.message.text.strip()
    if len(description) > 1000:
        await update.effective_message.reply_text("توضیحات بیش از حد طولانی است (حداکثر ۱۰۰۰ کاراکتر).")
        return AD_DESCRIPTION
    context.user_data['ad']['description'] = description
    await update.effective_message.reply_text("لطفاً قیمت خودرو یا حواله را به تومان وارد کنید:")
    logger.info(f"User {update.effective_user.id} submitted ad description")
    return AD_PRICE

async def receive_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = update.message.text.strip().replace(",", "")
    try:
        price_int = int(price)
        if price_int <= 0:
            raise ValueError("قیمت باید مثبت باشد")
        formatted_price = f"{price_int:,}"
        context.user_data['ad']['price'] = formatted_price
        if context.user_data['ad'].get('is_referral', 0):
            temp_title = context.user_data['ad']['temp_title']
            context.user_data['ad']['title'] = f"حواله‌ی {clean_text(temp_title)} با قیمت {formatted_price} تومان"
        else:
            context.user_data['ad']['title'] = context.user_data['ad']['temp_title']
        await update.effective_message.reply_text(
            "لطفاً عکس خودرو یا حواله را ارسال کنید (حداکثر ۵ تصویر) (یا 'تمام' برای اتمام یا 'هیچ' اگر عکسی ندارید):"
        )
        logger.info(f"User {update.effective_user.id} submitted ad price: {formatted_price}")
        return AD_PHOTOS
    except ValueError:
        await update.effective_message.reply_text("لطفاً قیمت را به صورت عددی و به تومان وارد کنید (مثال: 500000000).")
        return AD_PRICE

async def receive_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        logger.info(f"User {update.effective_user.id} uploaded photo {len(ad['photos'])}")
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
    try:
        with get_db_connection() as conn:
            user_data = conn.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,)).fetchone()
            if user_data and user_data['phone']:
                context.user_data['ad']['phone'] = user_data['phone']
                return await save_ad(update, context)
            if 'ad' not in context.user_data or not context.user_data['ad']:
                await update.effective_message.reply_text(
                    "⚠️ داده‌های آگهی یافت نشد. لطفاً از ابتدا آگهی را ثبت کنید.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return ConversationHandler.END
            keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton("📞 ارسال شماره", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await update.effective_message.reply_text(
                "📞 لطفاً شماره تلفن خود را برای ثبت آگهی یا حواله با زدن دکمه زیر ارسال کنید:",
                reply_markup=keyboard
            )
            logger.info(f"Requesting phone number from user {user_id}")
            return AD_PHONE
    except sqlite3.Error as e:
        logger.error(f"Database error in request_phone: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در بررسی اطلاعات رخ داد.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = None
    if update.message.contact:
        phone = update.message.contact.phone_number
    elif update.message.text:
        phone = update.message.text.strip()
    phone_pattern = r'^(\+98|0)?9\d{9}$'
    cleaned_phone = phone.replace('-', '').replace(' ', '') if phone else ''
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
    try:
        with get_db_connection() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO users (user_id, phone) VALUES (?, ?)',
                (user_id, cleaned_phone)
            )
            conn.commit()
            if 'ad' not in context.user_data or not context.user_data['ad']:
                await update.effective_message.reply_text(
                    "⚠️ داده‌های آگهی یافت نشد. لطفاً از ابتدا آگهی را ثبت کنید.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return ConversationHandler.END
            context.user_data['ad']['phone'] = cleaned_phone
            await update.effective_message.reply_text(
                "✅ شماره تلفن با موفقیت ثبت شد. آگهی یا حواله شما در حال ارسال برای تأیید است...",
                reply_markup=ReplyKeyboardRemove()
            )
            logger.info(f"User {user_id} submitted phone: {cleaned_phone}")
            return await save_ad(update, context)
    except sqlite3.Error as e:
        logger.error(f"Database error in receive_phone: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در ثبت اطلاعات رخ داد. لطفاً دوباره تلاش کنید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

async def save_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad = context.user_data['ad']
    user_id = update.effective_user.id
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO ads 
                (user_id, title, description, price, photos, created_at, is_referral) 
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (
                    user_id,
                    ad['title'],
                    ad['description'],
                    ad['price'],
                    ','.join(ad['photos']) if ad['photos'] else '',
                    datetime.now().isoformat(),
                    ad['is_referral']
                )
            )
            ad_id = cursor.lastrowid
            conn.commit()
            for admin_id in ADMIN_ID:
                try:
                    await send_message_with_rate_limit(
                        context.bot,
                        admin_id,
                        text=f"📢 {'حواله' if ad['is_referral'] else 'آگهی'} جدید ثبت شد:\nعنوان: {clean_text(ad['title'])}\nشناسه: {ad_id}\nلطفاً در پنل مدیریت بررسی کنید.",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Error notifying admin {admin_id}: {e}")
            await update.effective_message.reply_text(
                f"با تشکر از اعتماد شما. ✅ {'حواله' if ad['is_referral'] else 'آگهی'} با موفقیت ثبت شد و در انتظار تأیید مدیر است.\n"
                "می‌توانید از منوی اصلی برای ثبت آگهی یا حواله جدید ادامه دهید."
            )
            logger.info(f"Ad {ad_id} saved for user {user_id}")
            context.user_data.clear()
            return ConversationHandler.END
    except sqlite3.Error as e:
        logger.error(f"Database error in save_ad: {e}")
        await update.effective_message.reply_text(
            "❌ خطایی در ثبت آگهی یا حواله رخ داد، لطفاً دوباره تلاش کنید."
        )
        return ConversationHandler.END

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except BadRequest as e:
            logger.warning(f"Failed to answer callback query: {e}")
            await query.message.reply_text("لطفاً دوباره تلاش کنید.")
            return
        message = query.message
    else:
        message = update.effective_message
    if update.effective_user.id not in ADMIN_ID:
        await message.reply_text("❌ دسترسی غیرمجاز!")
        return
    page = context.user_data.get('admin_page', 1)
    items_per_page = 5
    status_filter = context.user_data.get('admin_status_filter', 'pending')
    try:
        with get_db_connection() as conn:
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
                    text=f"هیچ {'حواله یا آگهی' if status_filter == 'pending' else status_filter} با وضعیت '{status_filter}' یافت نشد.",
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
                phone = user_info['phone'] if user_info and user_info['phone'] else "نامشخص"
                price = ad['price'] if ad['price'] else "نامشخص"
                try:
                    user = await context.bot.get_chat(ad['user_id'])
                    username = user.username or f"{user.first_name} {user.last_name or ''}".strip() or "ناشناس"
                except Exception:
                    username = "ناشناس"
                ad_text = (
                    f"{'📜 حواله' if ad['is_referral'] else '🆔 آگهی'}: {ad['id']}\n"
                    f"👤 کاربر: {clean_text(username)}\n"
                    f"📞 شماره تماس: {clean_text(phone)}\n"
                    f"📌 عنوان: {clean_text(ad['title'])}\n"
                    f"💬 توضیحات: {clean_text(ad['description'])}\n"
                    f"💰 قیمت: {clean_text(price)} تومان\n"
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
        logger.error(f"Error in admin panel: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آگهی‌ها یا حواله‌ها رخ داد."
        )

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Failed to answer callback query: {e}")
        await query.message.reply_text("لطفاً دوباره تلاش کنید.")
        return
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی غیرمجاز!")
        return
    action, ad_id = query.data.split('_')
    ad_id = int(ad_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            ad = cursor.execute(
                'SELECT user_id, title, description, price, photos, status, created_at, is_referral FROM ads WHERE id = ?', 
                (ad_id,)
            ).fetchone()
            if not ad:
                await query.message.reply_text("❌ آگهی یا حواله یافت نشد!")
                return
            if action == "approve":
                new_status = "approved"
                user_message = f"✅ {'حواله' if ad['is_referral'] else 'آگهی'} شما *{clean_text(ad['title'])}* تأیید شد و در کانال منتشر شد."
                title = clean_text(ad['title'])
                description = clean_text(ad['description'])
                ad_text = (
                    f"{'📜 حواله' if ad['is_referral'] else '🚗 آگهی'}: {title}\n\n"
                    f"📝 توضیحات: {description}\n\n"
                    f"➖➖➖➖➖\n"
                    f"☑️ اتوگالری بلوری\n"
                    f"▫️خرید▫️فروش▫️کارشناسی\n"
                    f"📲 +989153632957\n"
                    f"📍 @{CHANNEL_USERNAME}"
                )
                try:
                    if ad['photos']:
                        photos = ad['photos'].split(',')
                        await send_message_with_rate_limit(
                            context.bot,
                            CHANNEL_ID,
                            photo=photos[0],
                            text=ad_text,
                            parse_mode='Markdown'
                        )
                        for photo in photos[1:3]:
                            await send_message_with_rate_limit(
                                context.bot,
                                CHANNEL_ID,
                                photo=photo
                            )
                    else:
                        await send_message_with_rate_limit(
                            context.bot,
                            CHANNEL_ID,
                            text=ad_text,
                            parse_mode='Markdown'
                        )
                    users = cursor.execute('SELECT user_id FROM users').fetchall()
                    for user in users:
                        user_id = user['user_id']
                        if user_id != ad['user_id']:
                            try:
                                if ad['photos']:
                                    await send_message_with_rate_limit(
                                        context.bot,
                                        user_id,
                                        text=ad_text,
                                        photo=photos[0],
                                        parse_mode='Markdown'
                                    )
                                    for photo in photos[1:3]:
                                        await send_message_with_rate_limit(
                                            context.bot,
                                            user_id,
                                            photo=photo
                                        )
                                else:
                                    await send_message_with_rate_limit(
                                        context.bot,
                                        user_id,
                                        text=ad_text,
                                        parse_mode='Markdown'
                                    )
                            except Exception as e:
                                logger.error(f"Error sending ad to user {user_id}: {e}")
                except TelegramError as e:
                    logger.error(f"Error publishing ad to channel/users: {e}")
                    await query.message.reply_text("❌ خطایی در انتشار آگهی یا حواله رخ داد.")
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
                        text=user_message,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Error notifying user {ad['user_id']}: {e}")
                await query.message.reply_text(f"✅ {'حواله' if ad['is_referral'] else 'آگهی'} {ad_id} تأیید و در کانال و برای کاربران منتشر شد.")
            elif action == "reject":
                new_status = "rejected"
                user_message = f"❌ {'حواله' if ad['is_referral'] else 'آگهی'} شما *{clean_text(ad['title'])}* رد شد."
                cursor.execute(
                    'UPDATE ads SET status = ? WHERE id = ?',
                    (new_status, ad_id)
                )
                conn.commit()
                try:
                    await send_message_with_rate_limit(
                        context.bot,
                        ad['user_id'],
                        text=user_message,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Error notifying user {ad['user_id']}: {e}")
                await query.message.reply_text(f"❌ {'حواله' if ad['is_referral'] else 'آگهی'} {ad_id} رد شد.")
            await admin_panel(update, context)
    except sqlite3.Error as e:
        logger.error(f"Database error in handle_admin_action: {e}")
        await query.message.reply_text("❌ خطایی در پردازش درخواست رخ داد.")

async def change_status_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Failed to answer callback query: {e}")
        await query.message.reply_text("لطفاً دوباره تلاش کنید.")
        return
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
        await send_message_with_rate_limit(
            context.bot,
            query.message.chat_id,
            text="📊 وضعیت مورد نظر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    elif data.startswith("status_"):
        context.user_data['admin_status_filter'] = data.split('_')[1]
        context.user_data['admin_page'] = 1
        await admin_panel(update, context)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Failed to answer callback query: {e}")
        await query.message.reply_text("لطفاً دوباره تلاش کنید.")
        return
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
        try:
            with get_db_connection() as conn:
                ad = conn.execute(
                    'SELECT photos, is_referral FROM ads WHERE id = ?', 
                    (ad_id,)
                ).fetchone()
                if ad and ad['photos']:
                    photos = ad['photos'].split(',')
                    for photo in photos[:5]:
                        await send_message_with_rate_limit(
                            context.bot,
                            update.effective_chat.id,
                            text=f"تصاویر {'حواله' if ad['is_referral'] else 'آگهی'} {ad_id}",
                            photo=photo
                        )
                else:
                    await query.message.reply_text("📸 این آگهی یا حواله هیچ تصویری ندارد.")
        except Exception as e:
            logger.error(f"Error showing photos: {e}")
            await query.message.reply_text("❌ خطایی در نمایش تصاویر رخ داد.")
    elif data == "admin_exit":
        await query.message.reply_text("🏠 بازگشت به منوی اصلی.")
        await start(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)

async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except BadRequest as e:
            logger.warning(f"Failed to answer callback query: {e}")
            await query.message.reply_text("لطفاً دوباره تلاش کنید.")
            return
        message = query.message
    else:
        message = update.effective_message
    one_year_ago = datetime.now() - timedelta(days=365)
    try:
        with get_db_connection() as conn:
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
                    text="هیچ آگهی یا حواله تأیید شده‌ای یافت نشد."
                )
                return
            for ad in ads:
                user_info = cursor.execute(
                    'SELECT phone FROM users WHERE user_id = ?', 
                    (ad['user_id'],)
                ).fetchone()
                phone = clean_text(user_info['phone'] if user_info else "ناشناس")
                text = (
                    f"{'📜 حواله' if ad['is_referral'] else '📌 آگهی'}: {clean_text(ad['title'])}\n"
                    f"💬 توضیحات: {clean_text(ad['description'])}\n"
                    f"💰 قیمت: {clean_text(ad['price'])} تومان\n"
                    f"📞 شماره تماس: {phone}\n"
                    f"📅 تاریخ: {ad['created_at']}"
                )
                try:
                    if ad['photos']:
                        photos = ad['photos'].split(',')
                        await send_message_with_rate_limit(
                            context.bot,
                            update.effective_chat.id,
                            text=text,
                            photo=photos[0],
                            parse_mode='Markdown'
                        )
                        for photo in photos[1:3]:
                            await send_message_with_rate_limit(
                                context.bot,
                                update.effective_chat.id,
                                photo=photo
                            )
                    else:
                        await send_message_with_rate_limit(
                            context.bot,
                            update.effective_chat.id,
                            text=text,
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    logger.error(f"Error sending ad {ad['id']}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in show_ads: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آگهی‌ها یا حواله‌ها رخ داد."
        )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except BadRequest as e:
            logger.warning(f"Failed to answer callback query: {e}")
            await query.message.reply_text("لطفاً دوباره تلاش کنید.")
            return
        message = query.message
    else:
        message = update.effective_message
    if update.effective_user.id not in ADMIN_ID:
        await message.reply_text("❌ دسترسی غیرمجاز!")
        return
    try:
        with get_db_connection() as conn:
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
            referral_ads = cursor.execute(
                'SELECT COUNT(*) FROM ads WHERE is_referral = 1'
            ).fetchone()[0]
            total_admins = cursor.execute('SELECT COUNT(*) FROM admins').fetchone()[0]
            stats_text = (
                "📊 آمار ربات:\n\n"
                f"👥 تعداد کل کاربران: {total_users}\n"
                f"🆕 کاربران جدید امروز: {new_users_today}\n\n"
                f"📝 تعداد کل آگهی‌ها و حواله‌ها: {total_ads}\n"
                f"📜 تعداد حواله‌ها: {referral_ads}\n"
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
        logger.error(f"Database error in stats: {e}")
        await send_message_with_rate_limit(
            context.bot,
            update.effective_chat.id,
            text="❌ خطایی در نمایش آمار رخ داد."
        )

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    try:
        with get_db_connection() as conn:
            conn.execute('INSERT INTO admins (user_id) VALUES (?)', (new_admin_id,))
            conn.commit()
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
            logger.error(f"Error notifying new admin {new_admin_id}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in add_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در افزودن مدیر رخ داد.")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM admins WHERE user_id = ?', (admin_id_to_remove,))
            conn.commit()
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
            logger.error(f"Error notifying removed admin {admin_id_to_remove}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in remove_admin: {e}")
        await update.effective_message.reply_text("❌ خطایی در حذف مدیر رخ داد.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text(
        "❌ عملیات فعلی لغو شد.",
        reply_markup=ReplyKeyboardRemove()
    )
    logger.info(f"User {update.effective_user.id} cancelled operation")
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error processing update {update}: {context.error}", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید."
            )
        except Exception:
            pass

async def show_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as e:
        logger.warning(f"Failed to answer callback query: {e}")
        await query.message.reply_text("لطفاً دوباره تلاش کنید.")
        return
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
    try:
        with get_db_connection() as conn:
            ad = conn.execute(
                'SELECT photos, is_referral FROM ads WHERE id = ?', 
                (ad_id,)
            ).fetchone()
            if not ad or not ad['photos']:
                await query.message.reply_text("این آگهی یا حواله عکسی ندارد!")
                return
            photos = ad['photos'].split(',')
            for photo in photos[:5]:
                await send_message_with_rate_limit(
                    context.bot,
                    query.message.chat_id,
                    photo=photo,
                    text=f"تصویر {'حواله' if ad['is_referral'] else 'آگهی'} {ad_id}"
                )
    except Exception as e:
        logger.error(f"Error showing photos for ad {ad_id}: {e}")
        await query.message.reply_text("❌ خطایی در نمایش تصاویر رخ داد.")

# ==================== تنظیمات اولیه ====================

init_db()
update_admin_ids()

async def main():
    logger.info("Starting main function...")
    try:
        application = get_application()
        logger.info("Application initialized.")
        logger.info("Starting polling...")
        await application.start_polling()
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    logger.info("Running main...")
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Error running main: {e}", exc_info=True)
