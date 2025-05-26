import logging
import os
import json
import re
import asyncio
import sqlite3
from datetime import datetime
import time
from threading import Lock
from dotenv import load_dotenv
import telegram
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, KeyboardButton, ReplyKeyboardMarkup
from telegram.error import TelegramError, Forbidden, BadRequest
from logging.handlers import RotatingFileHandler

load_dotenv()

handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=2)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.DEBUG, handlers=[handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)
logging.getLogger('telegram').setLevel(logging.DEBUG)
logging.getLogger('httpx').setLevel(logging.DEBUG)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@bolori_car")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/bolori_car")

logger.info(f"BOT_TOKEN: {BOT_TOKEN}")
logger.info(f"CHANNEL_ID: {CHANNEL_ID}")
logger.info(f"CHANNEL_URL: {CHANNEL_URL}")

missing_vars = []
if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")
if not CHANNEL_ID: missing_vars.append("CHANNEL_ID")
if not CHANNEL_URL: missing_vars.append("CHANNEL_URL")
if missing_vars:
    logger.error(f"Missing environment variables: {', '.join(missing_vars)}")
    raise ValueError(f"Missing environment variables: {', '.join(missing_vars)}")

APPLICATION = None
ADMIN_ID = [5677216420]
current_pages = {}
MAIN_INITIALIZED = False
INIT_LOCK = Lock()

DATABASE_PATH = "./data/database.db"
DB_CONNECTION = None

def init_db_connection():
    global DB_CONNECTION
    try:
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        DB_CONNECTION = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=10)
        DB_CONNECTION.row_factory = sqlite3.Row
        logger.debug("Database connection initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database connection: {e}")
        raise

def init_db():
    logger.debug("Initializing database...")
    try:
        with DB_CONNECTION:
            DB_CONNECTION.execute('''CREATE TABLE IF NOT EXISTS users
                          (user_id INTEGER PRIMARY KEY, joined TEXT, blocked INTEGER DEFAULT 0, username TEXT)''')
            DB_CONNECTION.execute('''CREATE TABLE IF NOT EXISTS ads
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
                           title TEXT, description TEXT, price INTEGER, created_at TEXT,
                           status TEXT, image_id TEXT, phone TEXT)''')
            DB_CONNECTION.execute('''CREATE TABLE IF NOT EXISTS admins
                          (user_id INTEGER PRIMARY KEY)''')
            DB_CONNECTION.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (5677216420,))
            DB_CONNECTION.execute('''CREATE INDEX IF NOT EXISTS idx_ads_status ON ads (status)''')
            DB_CONNECTION.execute('''CREATE INDEX IF NOT EXISTS idx_ads_approved ON ads (status, created_at DESC)''')
            DB_CONNECTION.execute('''CREATE INDEX IF NOT EXISTS idx_users_id ON users (user_id)''')
            DB_CONNECTION.commit()
            logger.debug("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization failed: {e}")
        raise

def load_admins():
    logger.debug("Loading admin IDs...")
    with DB_CONNECTION:
        admins = DB_CONNECTION.execute('SELECT user_id FROM admins').fetchall()
        admin_ids = [admin['user_id'] for admin in admins]
        logger.debug(f"Loaded {len(admin_ids)} admin IDs")
        return admin_ids

def safe_json_loads(data):
    if not data:
        return []
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in image_id: {data}")
        return [data] if data else []

def translate_ad_type(ad_type):
    return "آگهی" if ad_type == "ad" else "حواله"

FSM_STATES = {}
FSM_LOCK = Lock()

async def cleanup_fsm_states():
    while True:
        with FSM_LOCK:
            for user_id in list(FSM_STATES.keys()):
                if "last_updated" not in FSM_STATES[user_id]:
                    FSM_STATES[user_id]["last_updated"] = time.time()
                elif time.time() - FSM_STATES[user_id]["last_updated"] > 3600:
                    del FSM_STATES[user_id]
                    logger.debug(f"Cleaned up FSM state for user {user_id}")
        await asyncio.sleep(600)

async def broadcast_ad(context: ContextTypes.DEFAULT_TYPE, ad):
    logger.debug(f"Broadcasting ad {ad['id']} to all users")
    try:
        with DB_CONNECTION:
            users = DB_CONNECTION.execute("SELECT user_id FROM users WHERE blocked = 0").fetchall()
        user_count = len(users)
        delay = 0.1 if user_count < 100 else 0.3
        images = safe_json_loads(ad['image_id'])
        ad_text = (
            f"🚗 {translate_ad_type(ad['type'])} جدید:\n"
            f"عنوان: {ad['title']}\n"
            f"توضیحات: {ad['description']}\n"
            f"قیمت: {ad['price']:,} تومان\n"
            f"📢 برای جزئیات بیشتر به ربات مراجعه کنید: @Bolori_car_bot\n"
            f"""➖➖➖➖➖
☑️ اتوگالــری بلـــوری
▫️خرید▫️فروش▫کارشناسی
+989153632957
➖➖➖➖
@Bolori_Car
جهت ثبت آگهی تان به ربات زیر مراجعه کنید.
@bolori_car_bot"""
        )
        for user in users:
            try:
                if images:
                    media = [InputMediaPhoto(media=photo, caption=ad_text if i == 0 else None)
                             for i, photo in enumerate(images)]
                    await context.bot.send_media_group(chat_id=user['user_id'], media=media)
                else:
                    await context.bot.send_message(chat_id=user['user_id'], text=ad_text)
                await asyncio.sleep(delay)
            except Forbidden:
                logger.warning(f"User {user['user_id']} has blocked the bot.")
                with DB_CONNECTION:
                    DB_CONNECTION.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user['user_id'],))
                    DB_CONNECTION.commit()
            except Exception as e:
                logger.error(f"Error broadcasting ad to user {user['user_id']}: {e}")
    except Exception as e:
        logger.error(f"Error in broadcast_ad: {e}")

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Checking membership for user {user_id} in channel {CHANNEL_ID}")
    try:
        chat_member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if chat_member.status in ['member', 'administrator', 'creator']:
            logger.debug(f"User {user_id} is a member of channel {CHANNEL_ID}")
            return True
        logger.debug(f"User {user_id} is not a member of channel {CHANNEL_ID}")
        return False
    except TelegramError as e:
        logger.error(f"Error checking membership for user {user_id}: {e}")
        if isinstance(e, Forbidden):
            logger.warning(f"Bot does not have permission to check membership in {CHANNEL_ID}")
        await update.effective_message.reply_text(
            "❌ خطایی در بررسی عضویت رخ داد. لطفاً مطمئن شوید که در کانال عضو هستید."
        )
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Start command received from user {update.effective_user.id}")
    user = update.effective_user
    if await check_membership(update, context):
        buttons = [
            [InlineKeyboardButton("➕ ثبت آگهی", callback_data="post_ad")],
            [InlineKeyboardButton("📜 ثبت حواله", callback_data="post_referral")],
            [InlineKeyboardButton("🗂️ نمایش آگهی‌ها", callback_data="show_ads_ad")],
            [InlineKeyboardButton("📋 نمایش حواله‌ها", callback_data="show_ads_referral")]
        ]
        if user.id in ADMIN_ID:
            buttons.extend([
                [InlineKeyboardButton("📋 بررسی آگهی‌ها", callback_data="review_ads_ad")],
                [InlineKeyboardButton("📋 بررسی حواله‌ها", callback_data="review_ads_referral")],
                [InlineKeyboardButton("📊 آمار کاربران", callback_data="stats")],
                [InlineKeyboardButton("📢 ارسال پیام به همه", callback_data="broadcast_message")],
                [InlineKeyboardButton("🚫 کاربران بلاک‌کننده", callback_data="blocked_users")]
            ])
        welcome_text = (
            f"سلام {user.first_name} عزیز! 👋\n\n"
            "به ربات رسمی ثبت آگهی و حواله خودرو *اتوگالری بلوری* خوش آمدید. از طریق این ربات می‌توانید:\n"
            "  - آگهی فروش خودروی خود را به‌صورت مرحله‌به‌مرحله ثبت کنید\n"
            "  - حواله خودرو ثبت کنید\n"
            "  - آگهی‌های ثبت‌شده را مشاهده و جست‌وجو کنید\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:\n\n"
        )
        await update.effective_message.reply_text(
            welcome_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown"
        )
        try:
            with DB_CONNECTION:
                DB_CONNECTION.execute(
                    'INSERT OR REPLACE INTO users (user_id, joined, blocked, username) VALUES (?, ?, 0, ?)',
                    (user.id, datetime.now().isoformat(), user.username)
                )
                DB_CONNECTION.commit()
                logger.debug(f"User {user.id} registered in database")
        except sqlite3.Error as e:
            logger.error(f"Database error in start: {e}")
            await update.effective_message.reply_text("❌ خطایی در ثبت اطلاعات رخ داد.")
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ عضویت در کانال", url=CHANNEL_URL)],
            [InlineKeyboardButton("🔄 بررسی عضویت", callback_data="check_membership")]
        ])
        await update.effective_message.reply_text(
            "⚠️ برای استفاده از ربات، لطفاً ابتدا در کانال ما عضو شوید:",
            reply_markup=keyboard
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with FSM_LOCK:
        if user_id in FSM_STATES:
            del FSM_STATES[user_id]
            await update.message.reply_text("فرآیند لغو شد. برای شروع دوباره، /start را بزنید.")
        else:
            await update.message.reply_text("هیچ فرآیند فعالی وجود ندارد.")

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Admin command received from user {user_id}")
    if user_id in ADMIN_ID:
        buttons = [
            [InlineKeyboardButton("📋 بررسی آگهی‌ها", callback_data="review_ads_ad")],
            [InlineKeyboardButton("📋 بررسی حواله‌ها", callback_data="review_ads_referral")],
            [InlineKeyboardButton("📊 آمار کاربران", callback_data="stats")],
            [InlineKeyboardButton("📢 ارسال پیام به همه", callback_data="broadcast_message")],
            [InlineKeyboardButton("🚫 کاربران بلاک‌کننده", callback_data="blocked_users")]
        ]
        await update.effective_message.reply_text(
            "پنل ادمین:\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        logger.debug(f"User {user_id} is not an admin")
        await update.effective_message.reply_text("هشدار: شما دسترسی ادمین ندارید.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Stats command received from user {user_id}")
    if user_id in ADMIN_ID:
        try:
            with DB_CONNECTION:
                user_count = DB_CONNECTION.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                ad_count = DB_CONNECTION.execute("SELECT COUNT(*) FROM ads WHERE status = 'approved'").fetchone()[0]
            stats_text = (
                f"📊 آمار ربات:\n"
                f"تعداد کاربران: {user_count}\n"
                f"تعداد آگهی‌های تأییدشده: {ad_count}"
            )
            await update.effective_message.reply_text(stats_text)
        except sqlite3.Error as e:
            logger.error(f"Database error in stats: {e}")
            await update.effective_message.reply_text("❌ خطایی در دریافت آمار رخ داد.")
    else:
        logger.debug(f"User {user_id} is not an admin")
        await update.effective_message.reply_text("هشدار: شما دسترسی ادمین ندارید.")

async def post_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Post ad started for user {user_id}")
    with FSM_LOCK:
        FSM_STATES[user_id] = {"state": "post_ad_title", "last_updated": time.time()}
    await update.effective_message.reply_text("لطفاً برند و مدل خودروی خود را وارد نمایید (مثلاً: فروش پژو207 پانا):")

async def post_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Post referral started for user {user_id}")
    with FSM_LOCK:
        FSM_STATES[user_id] = {"state": "post_referral_title", "last_updated": time.time()}
    await update.effective_message.reply_text("لطفاً عنوان حواله را وارد کنید (مثال: حواله پژو 207):")

async def post_ad_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.effective_message
    with FSM_LOCK:
        state = FSM_STATES.get(user_id, {}).get("state")
        if user_id in FSM_STATES:
            FSM_STATES[user_id]["last_updated"] = time.time()
    logger.debug(f"Handling message for user {user_id} in state {state}")
    try:
        if state == "post_ad_title":
            with FSM_LOCK:
                FSM_STATES[user_id]["title"] = message.text
                FSM_STATES[user_id]["state"] = "post_ad_description"
            await update.message.reply_text("لطفاً اطلاعات خودرو شامل رنگ، کارکرد، وضعیت بدنه، وضعیت فنی و غیره را وارد نمایید.")
        elif state == "post_ad_description":
            with FSM_LOCK:
                FSM_STATES[user_id]["description"] = message.text
                FSM_STATES[user_id]["state"] = "post_ad_price"
            await update.message.reply_text("لطفاً قیمت آگهی را به تومان وارد کنید (فقط عدد):")
        elif state == "post_ad_price":
            try:
                price = int(message.text)
                with FSM_LOCK:
                    FSM_STATES[user_id]["price"] = price
                    FSM_STATES[user_id]["state"] = "post_ad_phone"
                keyboard = ReplyKeyboardMarkup(
                    [[KeyboardButton("📞 ارسال شماره تماس", request_contact=True)]],
                    one_time_keyboard=True,
                    resize_keyboard=True
                )
                await update.message.reply_text(
                    "لطفاً شماره تماس خود را ارسال کنید یا روی دکمه زیر کلیک کنید:",
                    reply_markup=keyboard
                )
            except ValueError:
                await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
        elif state == "post_ad_phone":
            phone_number = message.contact.phone_number if message.contact else message.text.strip() if message.text else None
            if not phone_number:
                await update.message.reply_text("لطفاً شماره تلفن خود را ارسال کنید.")
                return
            if re.match(r"^(09\d{9}|\+98\d{10}|98\d{10})$", phone_number):
                with FSM_LOCK:
                    FSM_STATES[user_id]["phone"] = phone_number
                    FSM_STATES[user_id]["state"] = "post_ad_image"
                    FSM_STATES[user_id]["images"] = []
                await update.message.reply_text(
                    "لطفاً تصاویر خودرو را ارسال کنید (حداکثر 5 عدد). پس از اتمام، /done را بزنید.",
                    reply_markup=ReplyKeyboardMarkup([])
                )
            else:
                await update.message.reply_text("⚖️ شماره تلفن نامعتبر است. باید با 09 یا +98 یا 98 شروع شود.")
        elif state == "post_ad_image":
            if message.text == "/done":
                if not FSM_STATES[user_id].get("images"):
                    await update.message.reply_text("لطفاً حداقل یک عکس ارسال کنید یا /cancel بزنید.")
                    return
                try:
                    with DB_CONNECTION:
                        cursor = DB_CONNECTION.cursor()
                        cursor.execute(
                            "INSERT INTO ads (user_id, type, title, description, price, image_id, phone, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (user_id, "ad", FSM_STATES[user_id]["title"], FSM_STATES[user_id]["description"],
                             FSM_STATES[user_id]["price"], json.dumps(FSM_STATES[user_id]["images"]),
                             FSM_STATES[user_id]["phone"], "pending")
                        )
                        ad_id = cursor.lastrowid
                        DB_CONNECTION.commit()
                        logger.debug(f"Ad saved for user {user_id} with id {ad_id}")
                    await message.reply_text("✅ آگهی شما ثبت شد و در انتظار تأیید ادمین است.")
                    username = update.effective_user.username or "بدون نام کاربری"
                    ad_text = (
                        f"🚖️ آگهی جدید از کاربر {user_id}:\n"
                        f"نام کاربری: @{username}\n"
                        f"شماره تماس: {FSM_STATES[user_id]['phone']}\n"
                        f"عنوان: {FSM_STATES[user_id]['title']}\n"
                        f"توضیحات: {FSM_STATES[user_id]['description']}\n"
                        f"💰 قیمت: {FSM_STATES[user_id]['price']:,}\n"
                        f"تعداد عکس‌ها: {len(FSM_STATES[user_id]['images'])}"
                    )
                    buttons = [
                        [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_ad_{ad_id}"),
                         InlineKeyboardButton("❌ رد", callback_data=f"reject_ad_{ad_id}")]
                    ]
                    for admin_id in ADMIN_ID:
                        try:
                            if FSM_STATES[user_id]['images']:
                                media = [InputMediaPhoto(media=photo, caption=ad_text if i == 0 else None)
                                         for i, photo in enumerate(FSM_STATES[user_id]["images"])]
                                await context.bot.send_media_group(chat_id=admin_id, media=media)
                                await context.bot.send_message(chat_id=admin_id, text="لطفاً آگهی را بررسی کنید:",
                                                              reply_markup=InlineKeyboardMarkup(buttons))
                            else:
                                await context.bot.send_message(chat_id=admin_id, text=ad_text,
                                                              reply_markup=InlineKeyboardMarkup(buttons))
                        except Exception as e:
                            logger.error(f"Error notifying admin {admin_id}: {e}")
                    with FSM_LOCK:
                        del FSM_STATES[user_id]
                except Exception as e:
                    logger.error(f"Error saving ad for user {user_id}: {e}")
                    await update.message.reply_text("❌ خطا در ثبت آگهی رخ داد.")
            elif message.photo:
                if len(FSM_STATES[user_id]["images"]) >= 5:
                    await update.message.reply_text("حداکثر 5 عکس قابل ارسال است. لطفاً /done را بزنید.")
                    return
                photo = message.photo[-1].file_id
                with FSM_LOCK:
                    FSM_STATES[user_id]["images"].append(photo)
                await update.message.reply_text(f"عکس {len(FSM_STATES[user_id]['images'])} دریافت شد. عکس بعدی یا /done")
            else:
                await update.message.reply_text("لطفاً فقط عکس ارسال کنید یا برای اتمام /done را بزنید.")
    except Exception as e:
        logger.error(f"Error in post_ad_handle_message for user {user_id}: {e}")
        await message.reply_text("😖 خطایی رخ داد.")

async def post_referral_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with FSM_LOCK:
        if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
            await update.message.reply_text("لطفاً فرآیند ثبت حواله را از ابتدا شروع کنید (/start).")
            return
        state = FSM_STATES[user_id]["state"]
        FSM_STATES[user_id]["last_updated"] = time.time()
    message = update.message
    try:
        if state == "post_referral_title":
            with FSM_LOCK:
                FSM_STATES[user_id]["title"] = message.text
                FSM_STATES[user_id]["state"] = "post_referral_description"
            await update.message.reply_text("لطفاً توضیحات حواله را وارد کنید:")
        elif state == "post_referral_description":
            with FSM_LOCK:
                FSM_STATES[user_id]["description"] = message.text
                FSM_STATES[user_id]["state"] = "post_referral_price"
            await update.message.reply_text("لطفاً قیمت حواله را به تومان وارد کنید (فقط عدد):")
        elif state == "post_referral_price":
            try:
                price = int(message.text)
                with FSM_LOCK:
                    FSM_STATES[user_id]["price"] = price
                    FSM_STATES[user_id]["state"] = "post_referral_phone"
                keyboard = ReplyKeyboardMarkup(
                    [[KeyboardButton("📞 ارسال شماره تماس", request_contact=True)]],
                    one_time_keyboard=True,
                    resize_keyboard=True
                )
                await update.message.reply_text(
                    "لطفاً شماره تماس خود را ارسال کنید یا روی دکمه زیر کلیک کنید:",
                    reply_markup=keyboard
                )
            except ValueError:
                await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
        elif state == "post_referral_phone":
            phone_number = message.contact.phone_number if message.contact else message.text.strip() if message.text else None
            if not phone_number:
                await update.message.reply_text("لطفاً شماره تلفن خود را وارد کنید.")
                return
            if re.match(r"^(09\d{9}|\+98\d{10}|98\d{10})$", phone_number):
                with FSM_LOCK:
                    FSM_STATES[user_id]["phone"] = phone_number
                await save_referral(update, context)
            else:
                await update.message.reply_text("⚠️ شماره تلفن نامعتبر است.")
    except Exception as e:
        logger.error(f"Error in post_referral_handle_message for user {user_id}: {e}")
        await update.message.reply_text("❌ خطایی رخ داد.")

async def save_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Saving referral for user {user_id}")
    try:
        with DB_CONNECTION:
            cursor = DB_CONNECTION.cursor()
            cursor.execute(
                '''INSERT INTO ads (user_id, type, title, description, price, created_at, status, phone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, "referral", FSM_STATES[user_id]["title"], FSM_STATES[user_id]["description"],
                 FSM_STATES[user_id]["price"], datetime.now().isoformat(), "pending", FSM_STATES[user_id]["phone"])
            )
            ad_id = cursor.lastrowid
            DB_CONNECTION.commit()
        await update.message.reply_text("🌟 حواله شما ثبت شد و در انتظار تأیید ادمین است.", reply_markup=ReplyKeyboardMarkup([]))
        username = update.effective_user.username or "بدون نام کاربری"
        for admin_id in ADMIN_ID:
            buttons = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_referral_{ad_id}"),
                 InlineKeyboardButton("❌ رد", callback_data=f"reject_referral_{ad_id}")]
            ]
            ad_text = (
                f"حواله جدید از کاربر {user_id}:\n"
                f"نام کاربری: @{username}\n"
                f"شماره تلفن: {FSM_STATES[user_id]['phone']}\n"
                f"عنوان: {FSM_STATES[user_id]['title']}\n"
                f"توضیحات: {FSM_STATES[user_id]['description']}\n"
                f"قیمت: {FSM_STATES[user_id]['price']:,} تومان"
            )
            await context.bot.send_message(chat_id=admin_id, text=ad_text, reply_markup=InlineKeyboardMarkup(buttons))
            await asyncio.sleep(1)
        with FSM_LOCK:
            del FSM_STATES[user_id]
    except Exception as e:
        logger.error(f"Error in save_referral: {str(e)}")
        await update.message.reply_text("❌ خطایی در ثبت حواله رخ داد.")

async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, ad_type=None):
    user_id = update.effective_user.id
    try:
        with DB_CONNECTION:
            if ad_type:
                total_ads = DB_CONNECTION.execute("SELECT COUNT(*) FROM ads WHERE status = 'approved' AND type = ?", (ad_type,)).fetchone()[0]
                ads = DB_CONNECTION.execute(
                    "SELECT * FROM ads WHERE status = 'approved' AND type = ? ORDER BY created_at DESC LIMIT 2 OFFSET ?",
                    (ad_type, page * 2)
                ).fetchall()
            else:
                total_ads = DB_CONNECTION.execute("SELECT COUNT(*) FROM ads WHERE status = 'approved'").fetchone()[0]
                ads = DB_CONNECTION.execute(
                    "SELECT * FROM ads WHERE status = 'approved' ORDER BY created_at DESC LIMIT 2 OFFSET ?",
                    (page * 2,)
                ).fetchall()
        if not ads:
            await context.bot.send_message(chat_id=user_id, text="📭 هیچ آیتمی برای نمایش موجود نیست.")
            return
        current_pages[user_id] = page
        keyboard = []
        if page > 0:
            keyboard.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"page_{page - 1}"))
        if (page + 1) * 2 < total_ads:
            keyboard.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"page_{page + 1}"))
        reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
        for ad in ads:
            images = safe_json_loads(ad['image_id'])
            ad_text = (
                f"🚗 {translate_ad_type(ad['type'])}: {ad['title']}\n"
                f"📝 توضیحات: {ad['description']}\n"
                f"💰 قیمت: {ad['price']:,} تومان\n"
                f"""➖➖➖➖
☑️ اتوگالــری
▫️خرید▫️فروش▫کارشناسی
+989153632957
"""
            )
            if images:
                media = [InputMediaPhoto(media=photo, caption=ad_text if i == 0 else None) for i, photo in enumerate(images)]
                await context.bot.send_media_group(chat_id=user_id, media=media)
            else:
                await context.bot.send_message(chat_id=user_id, text=ad_text)
            await asyncio.sleep(0.5)
        if reply_markup:
            await context.bot.send_message(chat_id=user_id, text=f"صفحه {page + 1} - تعداد آیتم‌ها: {total_ads}", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error showing ads: {e}")
        await context.bot.send_message(chat_id=user_id, text="❌ خطایی در نمایش آیتم‌ها رخ داد.")

async def review_ads(update: Update, context: ContextTypes.DEFAULT_TYPE, ad_type=None):
    user_id = update.effective_user.id
    if user_id not in ADMIN_ID:
        await context.bot.send_message(chat_id=user_id, text="هشدار: شما ادمین نیستید.")
        return
    try:
        with DB_CONNECTION:
            if ad_type:
                ads = DB_CONNECTION.execute(
                    "SELECT * FROM ads WHERE status = 'pending' AND type = ? ORDER BY created_at ASC LIMIT 1",
                    (ad_type,)
                ).fetchone()
            else:
                ads = DB_CONNECTION.execute(
                    "SELECT * FROM ads WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
            if not ads:
                await context.bot.send_message(chat_id=user_id, text=f"📪 هیچ {translate_ad_type(ad_type)} در انتظار تأیید نیست.")
                return
            images = safe_json_loads(ads['image_id'])
            ad_text = (
                f"📋 {translate_ad_type(ads['type'])}: {ads['title']}\n"
                f"توضیحات: {ads['description']}\n"
                f"شماره تماس: {ads['phone']}\n"
                f"قیمت: {ads['price']:,} تومان\n"
                f"کاربر: {user_id}"
            )
            buttons = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{ad_type}_{ads['id']}"),
                 InlineKeyboardButton("❌ رد", callback_data=f"reject_{ad_type}_{ads['id']}")]
            ]
            if images:
                await context.bot.send_photo(chat_id=user_id, photo=images[0], caption=ad_text, reply_markup=InlineKeyboardMarkup(buttons))
                for photo in images[1:]:
                    await context.bot.send_photo(chat_id=user_id, photo=photo)
                    await asyncio.sleep(0.5)
            else:
                await context.bot.send_message(chat_id=user_id, text=ad_text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"Error in review_ads: {e}")
        await context.bot.send_message(chat_id=user_id, text="❌ خطایی در بررسی آگهی‌ها رخ داد.")

async def message_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not update.message:
        logger.warning(f"Invalid update received")
        return
    with FSM_LOCK:
        if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
            await update.message.reply_text("لطفاً فرآیند را با /start شروع کنید.")
            return
        state = FSM_STATES[user_id]["state"]
        FSM_STATES[user_id]["last_updated"] = time.time()
    if state.startswith("post_ad"):
        await post_ad_handle_message(update, context)
    elif state.startswith("post_referral"):
        await post_referral_handle_message(update, context)
    elif state == "broadcast_message":
        if update.message.photo:
            photo = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            with FSM_LOCK:
                FSM_STATES[user_id]["broadcast_photo"] = photo
                FSM_STATES[user_id]["broadcast_caption"] = caption
        elif update.message.text:
            with FSM_LOCK:
                FSM_STATES[user_id]["broadcast_text"] = update.message.text
        else:
            await update.message.reply_text("لطفاً متن یا عکس بفرستید.")
            return
        buttons = [
            [InlineKeyboardButton("✅ ارسال به همه", callback_data="confirm_broadcast"),
             InlineKeyboardButton("❌ لغو", callback_data="cancel_broadcast")]
        ]
        await context.bot.send_message(chat_id=user_id, text="آیا می‌خواهید این پیام را به همه ارسال کنید؟",
                                      reply_markup=InlineKeyboardMarkup(buttons))
    else:
        logger.debug(f"Invalid state for user {user_id}: {state}")
        await update.message.reply_text("⚠️ حالت نامعتبر.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    user_id = query.from_user.id
    logger.debug(f"Callback received from user {user_id}: {callback_data}")
    if callback_data == "check_membership":
        if await check_membership(update, context):
            await start(update, context)
        else:
            await query.message.reply_text("⚠️ شما هنوز در کانال نیستید.")
    elif callback_data == "post_ad":
        await post_ad_start(update, context)
    elif callback_data == "post_referral":
        await post_referral_start(update, context)
    elif callback_data == "show_ads_ad":
        await show_ads(update, context, ad_type="ad")
    elif callback_data == "show_ads_referral":
        await show_ads(update, context, ad_type="referral")
    elif callback_data == "stats":
        await stats(update, context)
    elif callback_data == "broadcast_message":
        if user_id in ADMIN_ID:
            with FSM_LOCK:
                FSM_STATES[user_id] = {"state": "broadcast_message", "last_updated": time.time()}
            await query.message.reply_text("لطفاً پیام (متن یا عکس) را ارسال کنید.")
        else:
            await query.message.reply_text("هشدار: شما ادمین نیستید.")
    elif callback_data == "blocked_users":
        if user_id in ADMIN_ID:
            try:
                with DB_CONNECTION:
                    blocked_users = DB_CONNECTION.execute("SELECT user_id, username FROM users WHERE blocked = 1").fetchall()
                text = "کاربران بلاک‌کننده:\n" + "\n".join(
                    [f"ID: {user['user_id']}, Username: {user['username'] or 'ندارد'}" for user in blocked_users]
                ) if blocked_users else "هیچ کاربری ربات را بلاک نکرده است."
                await query.message.reply_text(text)
            except Exception as e:
                logger.error(f"Error fetching blocked users: {e}")
                await query.message.reply_text("❌ خطایی در نمایش کاربران بلاک‌کننده رخ داد.")
        else:
            await query.message.reply_text("هشدار: شما ادمین نیستید.")
    elif callback_data.startswith("approve_"):
        if user_id in ADMIN_ID:
            try:
                parts = callback_data.split("_")
                if len(parts) != 3:
                    raise ValueError("Invalid callback data")
                _, ad_type, ad_id = parts
                ad_id = int(ad_id)
                with DB_CONNECTION:
                    ad = DB_CONNECTION.execute(
                        "SELECT id, user_id, type, title, description, price FROM ads WHERE id = ?",
                        (ad_id,)
                    ).fetchone()
                    if not ad:
                        await query.message.reply_text("❌ آگهی یافت نشد.")
                        return
                    DB_CONNECTION.execute("UPDATE ads SET status = 'approved' WHERE id = ?", (ad_id,))
                    DB_CONNECTION.commit()
                await query.message.reply_text(f"✅ {translate_ad_type(ad_type)} تأیید شد.")
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=(
                        f"✅ {translate_ad_type(ad['type'])} شما تأیید شد:\n"
                        f"عنوان: {ad['title']}\n"
                        f"توضیحات: {ad['description']}\n"
                        f"قیمت: {ad['price']:,} تومان\n"
                        f"📢 برای مشاهده آگهی‌های دیگر، از دکمه 'نمایش آگهی‌ها' استفاده کنید."
                    )
                )
                asyncio.create_task(broadcast_ad(context, ad))
            except Exception as e:
                logger.error(f"Error in approve for ad {ad_id}: {e}")
                await query.message.reply_text("❌ خطایی در تأیید آگهی رخ داد.")
        else:
            await query.message.reply_text("هشدار: شما ادمین نیستید.")
    elif callback_data.startswith("reject_"):
        if user_id in ADMIN_ID:
            try:
                parts = callback_data.split("_")
                if len(parts) != 3:
                    raise ValueError("Invalid callback data")
                _, ad_type, ad_id = parts
                ad_id = int(ad_id)
                with DB_CONNECTION:
                    ad = DB_CONNECTION.execute("SELECT user_id FROM ads WHERE id = ?", (ad_id,)).fetchone()
                    if not ad:
                        await query.message.reply_text("❌ آگهی یافت نشد.")
                        return
                    DB_CONNECTION.execute("UPDATE ads SET status = 'rejected' WHERE id = ?", (ad_id,))
                    DB_CONNECTION.commit()
                await query.message.reply_text(f"❌ {translate_ad_type(ad_type)} رد شد.")
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=f"❌ {translate_ad_type(ad_type)} شما رد شد. لطفاً با ادمین تماس بگیرید."
                )
            except Exception as e:
                logger.error(f"Error in reject for ad {ad_id}: {e}")
                await query.message.reply_text("❌ خطایی در رد آگهی رخ داد.")
        else:
            await query.message.reply_text("هشدار: شما ادمین نیستید.")
    else:
        logger.warning(f"Unknown callback data: {callback_data}")
        await query.message.reply_text("⚠️ داده نامعتبر.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ خطایی در پردازش درخواست شما رخ داد.")
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}")

async def handle_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    page = int(query.data.split("_")[1])
    try:
        await query.message.delete()
    except BadRequest as e:
        logger.warning(f"Failed to delete message: {e}")
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
    await show_ads(update, context, page=page)

def get_application():
    logger.debug("Building application...")
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("cancel", cancel))
        application.add_handler(CommandHandler("admin", admin))
        application.add_handler(CommandHandler("stats", stats))
        application.add_handler(CallbackQueryHandler(handle_callback))
        application.add_handler(CallbackQueryHandler(handle_page_callback, pattern=r"^page_\d+$"))
        application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.CONTACT | filters.COMMAND, message_dispatcher))
        application.add_error_handler(error_handler)
        logger.debug("Application built successfully.")
        return application
    except Exception as e:
        logger.error(f"Error building application: {str(e)}")
        raise

async def init_main():
    global MAIN_INITIALIZED, APPLICATION, ADMIN_ID
    with INIT_LOCK:
        if not MAIN_INITIALIZED:
            logger.debug("Initializing main function...")
            init_db_connection()
            init_db()
            ADMIN_ID = load_admins()
            APPLICATION = get_application()
            await APPLICATION.initialize()
            await APPLICATION.start_polling()
            asyncio.create_task(cleanup_fsm_states())
            MAIN_INITIALIZED = True

def initialize_app():
    logger.debug("Running app initialization...")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(init_main())
        loop.run_forever()
    except Exception as e:
        logger.error(f"Failed to initialize app: {str(e)}")
        raise

if __name__ == '__main__':
    initialize_app()
