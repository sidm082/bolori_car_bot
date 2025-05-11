import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError
from aiohttp import web
import queue
import asyncio
import sqlite3
from datetime import datetime
import time
import os
import json
import re

# تنظیم لاگ‌گیری با سطح DEBUG
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)
logging.getLogger('telegram').setLevel(logging.DEBUG)

# متغیرهای محیطی
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", 8080))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@bolori_car")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/bolori_car")

# بررسی متغیرهای محیطی
if not all([BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET, CHANNEL_ID, CHANNEL_URL]):
    logger.error("One or more environment variables are missing.")
    raise ValueError("Missing environment variables")

# متغیرهای جهانی
update_queue = queue.Queue()
app = web.Application()
APPLICATION = None
ADMIN_ID = [5677216420]
FSM_STATES = {}

# تابع اتصال به دیتابیس
def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

# تابع مقداردهی اولیه دیتابیس
def init_db():
    logger.debug("Initializing database...")
    with get_db_connection() as conn:
        logger.debug("Opening database connection...")
        conn.execute('''CREATE TABLE IF NOT EXISTS users
                      (user_id INTEGER PRIMARY KEY, joined TEXT)''')
        logger.debug("Users table created.")
        conn.execute('''CREATE TABLE IF NOT EXISTS ads
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
                       title TEXT, description TEXT, price INTEGER, created_at TEXT,
                       status TEXT, image_id TEXT)''')
        logger.debug("Ads table created.")
        conn.execute('''CREATE TABLE IF NOT EXISTS admins
                      (user_id INTEGER PRIMARY KEY)''')
        logger.debug("Admins table created.")
        conn.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (5677216420,))
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_ads_status
                      ON ads (status)''')
        logger.debug("Index created.")
        conn.commit()
        logger.debug("Database initialized successfully.")

# تابع بارگذاری ادمین‌ها
def load_admins():
    logger.debug("Loading admin IDs...")
    with get_db_connection() as conn:
        logger.debug("Opening database connection...")
        admins = conn.execute('SELECT user_id FROM admins').fetchall()
        admin_ids = [admin['user_id'] for admin in admins]
        logger.debug(f"Loaded {len(admin_ids)} admin IDs")
        return admin_ids

# مسیر Webhook
async def webhook(request):
    logger.debug("Received webhook request")
    start_time = time.time()
    if WEBHOOK_SECRET and request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
        logger.warning("Invalid webhook secret token")
        return web.Response(status=401, text='Unauthorized')
    try:
        json_data = await request.json()
        if not json_data:
            logger.error("Empty webhook data received")
            return web.Response(status=400, text='Bad Request')
        update_queue.put(json_data)
        logger.debug(f"Queue size after putting update: {update_queue.qsize()}")
        logger.info(f"Webhook update queued in {time.time() - start_time:.2f} seconds")
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return web.Response(status=500, text='Internal Server Error')

# مسیر سلامت
async def health_check(request):
    return web.Response(status=200, text='OK')

# تابع پردازش صف به‌روزرسانی‌ها
async def process_update_queue():
    logger.debug("Starting update queue processing task...")
    global APPLICATION
    if APPLICATION is None:
        logger.error("Application is not initialized in process_update_queue")
        return
    while True:
        try:
            json_data = update_queue.get_nowait()
            start_time = time.time()
            logger.debug(f"Processing update: {json_data}")
            update = Update.de_json(json_data, APPLICATION.bot)
            if update:
                await APPLICATION.process_update(update)
                logger.info(f"Processed update in {time.time() - start_time:.2f} seconds")
            else:
                logger.warning("Received invalid update data")
            update_queue.task_done()
        except queue.Empty:
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error processing queued update: {e}", exc_info=True)

# تابع بررسی عضویت (برای تست موقتاً غیرفعال شده)
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Checking membership for user {user_id} in channel {CHANNEL_ID}")
    logger.debug("Skipping membership check for testing")
    return True

# تابع دستور start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Start command received from user {update.effective_user.id}")
    user = update.effective_user
    if await check_membership(update, context):
        logger.debug(f"User {user.id} is a member, showing welcome message")
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
                logger.debug(f"User {user.id} registered in database")
        except sqlite3.Error as e:
            logger.error(f"Database error in start: {e}")
            await update.effective_message.reply_text("❌ خطایی در ثبت اطلاعات رخ داد.")
    else:
        logger.debug(f"User {user.id} is not a member, prompting to join channel")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ عضویت در کانال", url=CHANNEL_URL)],
            [InlineKeyboardButton("🔄 بررسی عضویت", callback_data="check_membership")]
        ])
        await update.effective_message.reply_text(
            "⚠️ برای استفاده از ربات، لطفاً ابتدا در کانال ما عضو شوید:",
            reply_markup=keyboard
        )

# تابع دستور admin
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Admin command received from user {user_id}")
    if user_id in ADMIN_ID:
        buttons = [
            [InlineKeyboardButton("📋 بررسی آگهی‌ها", callback_data="review_ads")],
            [InlineKeyboardButton("📊 آمار کاربران", callback_data="stats")]
        ]
        await update.effective_message.reply_text(
            "پنل ادمین:\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        logger.debug(f"User {user_id} is not an admin")
        await update.effective_message.reply_text("⚠️ شما دسترسی ادمین ندارید.")

# تابع دستور stats
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Stats command received from user {user_id}")
    if user_id in ADMIN_ID:
        try:
            with get_db_connection() as conn:
                user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                ad_count = conn.execute("SELECT COUNT(*) FROM ads WHERE status = 'approved'").fetchone()[0]
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
        await update.effective_message.reply_text("⚠️ شما دسترسی ادمین ندارید.")

# تابع ثبت آگهی
async def post_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Post ad started for user {user_id}")
    FSM_STATES[user_id] = {"state": "post_ad_title"}
    await update.effective_message.reply_text("لطفاً برند و مدل خودروی خود را وارد نمایید.(مثلاً: فروش پژو207 پانا):")

async def post_ad_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
        logger.debug(f"No FSM state for user {user_id}, ignoring message")
        return
    state = FSM_STATES[user_id]["state"]
    logger.debug(f"Handling message for user {user_id} in state {state}")

    try:
        if state == "post_ad_title":
            message_text = update.message.text
            FSM_STATES[user_id]["title"] = message_text
            FSM_STATES[user_id]["state"] = "post_ad_description"
            await update.message.reply_text("لطفا *اطلاعات خودرو* شامل رنگ ، کارکرد ، وضعیت بدنه ، وضعیت فنی و غیره را وارد نمایید.")
        elif state == "post_ad_description":
            message_text = update.message.text
            FSM_STATES[user_id]["description"] = message_text
            FSM_STATES[user_id]["state"] = "post_ad_price"
            await update.message.reply_text("*لطفاً قیمت آگهی را به تومان وارد کنید *(فقط عدد):")
        elif state == "post_ad_price":
            message_text = update.message.text
            try:
                price = int(message_text)
                FSM_STATES[user_id]["price"] = price
                FSM_STATES[user_id]["state"] = "post_ad_phone"
                await update.message.reply_text(
                    "لطفاً شماره تلفن خود را وارد کنید (با شروع 09 یا +98، مثال: 09123456789 یا +989123456789):"
                )
            except ValueError:
                await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
        elif state == "post_ad_phone":
            message_text = update.message.text
            if re.match(r"^(09|\+98)\d{9}$", message_text):
                FSM_STATES[user_id]["phone"] = message_text
                FSM_STATES[user_id]["state"] = "post_ad_image"
                await update.message.reply_text("کنون لطفاً یک یا چند تصویر واضح از خودرو ارسال نمایید. (حداکثر 5عدد)")
            else:
                await update.message.reply_text(
                    "⚠️ شماره تلفن باید با 09 یا +98 شروع شود و 11 یا 12 رقم باشد (مثال: 09123456789 یا +989123456789). لطفاً دوباره وارد کنید:"
                )
        elif state == "post_ad_image":
            if update.message.text and update.message.text == "/skip":
                logger.debug(f"User {user_id} skipped image upload")
                FSM_STATES[user_id]["image_id"] = None
                await save_ad(update, context)
            elif update.message.photo:
                logger.debug(f"User {user_id} sent photo: {update.message.photo}")
                FSM_STATES[user_id]["image_id"] = update.message.photo[-1].file_id
                await save_ad(update, context)
            else:
                logger.debug(f"Invalid input for image state from user {user_id}")
                await update.effective_message.reply_text("اکنون لطفاً یک یا چند تصویر واضح از خودرو ارسال نمایید. (حداکثر 5عدد)")
    except Exception as e:
        logger.error(f"Error in post_ad_handle_message for user {user_id}: {e}", exc_info=True)
        await update.effective_message.reply_text(" اگر❌ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید.درصورت حل نشدن مشکل با ادمین تماس بگیرید.")

async def save_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Saving ad for user {user_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                '''INSERT INTO ads (user_id, type, title, description, price, created_at, status, image_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    user_id,
                    "ad",
                    FSM_STATES[user_id]["title"],
                    FSM_STATES[user_id]["description"],
                    FSM_STATES[user_id]["price"],
                    datetime.now().isoformat(),
                    "pending",
                    FSM_STATES[user_id].get("image_id"),
                ),
            )
            ad_id = cursor.lastrowid
            conn.commit()
        logger.debug(f"Ad saved successfully for user {user_id} with ad_id {ad_id}")
        await update.message.reply_text("آگهی شما با موفقیت ثبت شد و پس از بررسی، در لیست آگهی‌ها نمایش داده خواهد شد. \n "
        " *از اعتماد شما متشکریم* ")
        # اطلاع به ادمین‌ها
        username = update.effective_user.username or "بدون نام کاربری"
        for admin_id in ADMIN_ID:
            buttons = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_ad_{ad_id}")],
                [InlineKeyboardButton("❌ رد", callback_data=f"reject_ad_{ad_id}")],
            ]
            ad_text = (
                f"آگهی جدید از کاربر {user_id}:\n"
                f"نام کاربری: @{username}\n"
                f"شماره تلفن: {FSM_STATES[user_id]['phone']}\n"
                f"عنوان: {FSM_STATES[user_id]['title']}\n"
                f"توضیحات: {FSM_STATES[user_id]['description']}\n"
                f"قیمت: {FSM_STATES[user_id]['price']} تومان"
            )
            if FSM_STATES[user_id].get("image_id"):
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=FSM_STATES[user_id]["image_id"],
                    caption=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
        del FSM_STATES[user_id]
    except sqlite3.Error as e:
        logger.error(f"Database error in save_ad for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در ثبت آگهی رخ داد.")
    except TelegramError as e:
        logger.error(f"Telegram error in save_ad for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در ارسال اعلان به ادمین رخ داد.")
    except Exception as e:
        logger.error(f"Unexpected error in save_ad for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در پردازش آگهی رخ داد.")

# تابع ثبت حواله
async def post_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Post referral started for user {user_id}")
    FSM_STATES[user_id] = {"state": "post_referral_title"}
    try:
        # بررسی وضعیت چت
        chat = await context.bot.get_chat(user_id)
        logger.debug(f"Chat status for user {user_id}: {chat.type}")
        await update.effective_message.reply_text("لطفاً عنوان حواله را وارد کنید (مثلاً: حواله سایپا):")
        logger.debug(f"Sent title prompt to user {user_id}")
    except Forbidden:
        logger.error(f"User {user_id} has blocked the bot")
        try:
            await update.effective_message.reply_text("⚠️ شما ربات را بلاک کرده‌اید. لطفاً ربات را از بلاک خارج کنید.")
        except Exception:
            pass
        del FSM_STATES[user_id]
    except TelegramError as e:
        logger.error(f"Telegram error in post_referral_start for user {user_id}: {e}", exc_info=True)
        try:
            await update.effective_message.reply_text("❌ خطایی در شروع فرآیند حواله رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.error(f"Failed to send error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]
    except Exception as e:
        logger.error(f"Unexpected error in post_referral_start for user {user_id}: {e}", exc_info=True)
        try:
            await update.effective_message.reply_text("❌ خطایی در پردازش درخواست رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.error(f"Failed to send error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]

async def post_referral_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
        logger.debug(f"No FSM state for user {user_id}, ignoring message")
        return
    state = FSM_STATES[user_id]["state"]
    message_text = update.message.text
    logger.debug(f"Handling message for user {user_id} in state {state}: {message_text}")

    try:
        # بررسی وضعیت چت
        chat = await context.bot.get_chat(user_id)
        logger.debug(f"Chat status for user {user_id}: {chat.type}")
        
        if state == "post_referral_title":
            logger.debug(f"Storing title for user {user_id}: {message_text}")
            FSM_STATES[user_id]["title"] = message_text
            FSM_STATES[user_id]["state"] = "post_referral_description"
            logger.debug(f"State changed to post_referral_description for user {user_id}")
            await update.message.reply_text("لطفاً توضیحات حواله را وارد کنید:")
            logger.debug(f"Sent description prompt to user {user_id}")
        elif state == "post_referral_description":
            logger.debug(f"Storing description for user {user_id}: {message_text}")
            FSM_STATES[user_id]["description"] = message_text
            FSM_STATES[user_id]["state"] = "post_referral_price"
            logger.debug(f"State changed to post_referral_price for user {user_id}")
            await update.message.reply_text("لطفاً قیمت حواله را به تومان وارد کنید (فقط عدد):")
            logger.debug(f"Sent price prompt to user {user_id}")
        elif state == "post_referral_price":
            try:
                price = int(message_text)
                logger.debug(f"Storing price for user {user_id}: {price}")
                FSM_STATES[user_id]["price"] = price
                FSM_STATES[user_id]["state"] = "post_referral_phone"
                logger.debug(f"State changed to post_referral_phone for user {user_id}")
                await update.message.reply_text(
                    "لطفاً شماره تلفن خود را وارد کنید (با شروع 09 یا +98، مثال: 09123456789 یا +989123456789):"
                )
                logger.debug(f"Sent phone prompt to user {user_id}")
            except ValueError:
                await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
                logger.debug(f"Invalid price input from user {user_id}: {message_text}")
        elif state == "post_referral_phone":
            if re.match(r"^(09|\+98)\d{9}$", message_text):
                logger.debug(f"Storing phone number for user {user_id}: {message_text}")
                FSM_STATES[user_id]["phone"] = message_text
                await save_referral(update, context)
                logger.debug(f"Valid phone number received from user {user_id}: {message_text}")
            else:
                await update.message.reply_text(
                    "⚠️ شماره تلفن باید با 09 یا +98 شروع شود و 11 یا 12 رقم باشد (مثال: 09123456789 یا +989123456789). لطفاً دوباره وارد کنید:"
                )
                logger.debug(f"Invalid phone number input from user {user_id}: {message_text}")
    except Forbidden:
        logger.error(f"User {user_id} has blocked the bot")
        try:
            await update.message.reply_text("⚠️ شما ربات را بلاک کرده‌اید. لطفاً ربات را از بلاک خارج کنید.")
        except Exception:
            logger.error(f"Failed to send block error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]
    except BadRequest as e:
        logger.error(f"Bad request error in post_referral_handle_message for user {user_id}: {e}", exc_info=True)
        try:
            await update.message.reply_text("❌ خطایی در پردازش پیام شما رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.error(f"Failed to send bad request error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]
    except TelegramError as e:
        logger.error(f"Telegram error in post_referral_handle_message for user {user_id}: {e}", exc_info=True)
        try:
            await update.message.reply_text("❌ خطایی در ارسال پیام رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.error(f"Failed to send telegram error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]
    except Exception as e:
        logger.error(f"Unexpected error in post_referral_handle_message for user {user_id}: {e}", exc_info=True)
        try:
            await update.message.reply_text("❌ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception:
            logger.error(f"Failed to send unexpected error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]


async def save_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Saving referral for user {user_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                '''INSERT INTO ads (user_id, type, title, description, price, created_at, status, image_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    user_id,
                    "referral",
                    FSM_STATES[user_id]["title"],
                    FSM_STATES[user_id]["description"],
                    FSM_STATES[user_id]["price"],
                    datetime.now().isoformat(),
                    "pending",
                    None,
                ),
            )
            ad_id = cursor.lastrowid
            conn.commit()
        logger.debug(f"Referral saved successfully for user {user_id} with ad_id {ad_id}")
        await update.message.reply_text("✅ حواله شما ثبت شد و در انتظار تأیید ادمین است.")
        logger.debug(f"Sent confirmation message to user {user_id}")
        # اطلاع به ادمین‌ها
        username = update.effective_user.username or "بدون نام کاربری"
        for admin_id in ADMIN_ID:
            buttons = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_referral_{ad_id}")],
                [InlineKeyboardButton("❌ رد", callback_data=f"reject_referral_{ad_id}")]
            ]
            ad_text = (
                f"حواله جدید از کاربر {user_id}:\n"
                f"نام کاربری: @{username}\n"
                f"شماره تلفن: {FSM_STATES[user_id]['phone']}\n"
                f"عنوان: {FSM_STATES[user_id]['title']}\n"
                f"توضیحات: {FSM_STATES[user_id]['description']}\n"
                f"قیمت: {FSM_STATES[user_id]['price']} تومان"
            )
            await context.bot.send_message(
                chat_id=admin_id,
                text=ad_text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            logger.debug(f"Sent referral notification to admin {admin_id}")
        del FSM_STATES[user_id]
    except sqlite3.Error as e:
        logger.error(f"Database error in save_referral for user {user_id}: {e}", exc_info=True)
        try:
            await update.message.reply_text("❌ خطایی در ثبت حواله رخ داد.")
        except Exception:
            logger.error(f"Failed to send database error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]
    except TelegramError as e:
        logger.error(f"Telegram error in save_referral for user {user_id}: {e}", exc_info=True)
        try:
            await update.message.reply_text("❌ خطایی در ارسال اعلان به ادمین رخ داد.")
        except Exception:
            logger.error(f"Failed to send telegram error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]
    except Exception as e:
        logger.error(f"Unexpected error in save_referral for user {user_id}: {e}", exc_info=True)
        try:
            await update.message.reply_text("❌ خطایی در پردازش حواله رخ داد.")
        except Exception:
            logger.error(f"Failed to send unexpected error message to user {user_id}", exc_info=True)
        del FSM_STATES[user_id]

# ... (بخش‌های دیگر کد بدون تغییر باقی می‌مانند)

# تابع مدیریت callbackها (بخش تأیید حواله اصلاح‌شده)
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    user_id = update.effective_user.id
    logger.debug(f"Callback received from user {user_id}: {callback_data}")

    if callback_data == "check_membership":
        if await check_membership(update, context):
            await start(update, context)
        else:
            await query.message.reply_text("⚠️ شما هنوز در کانال عضو نشده‌اید.")
    elif callback_data == "post_ad":
        await post_ad_start(update, context)
    elif callback_data == "post_referral":
        await post_referral_start(update, context)
    elif callback_data == "show_ads":
        await show_ads(update, context)
    elif callback_data == "admin_panel":
        await admin_panel(update, context)
    elif callback_data == "stats":
        await stats(update, context)
    elif callback_data == "review_ads":
        await review_ads(update, context)
    elif callback_data.startswith("approve_"):
        if user_id in ADMIN_ID:
            try:
                _, ad_type, ad_id = callback_data.split("_")
                ad_id = int(ad_id)
                with get_db_connection() as conn:
                    ad = conn.execute(
                        "SELECT user_id, title, description, price, image_id FROM ads WHERE id = ?",
                        (ad_id,),
                    ).fetchone()
                    if not ad:
                        logger.error(f"Ad with id {ad_id} not found")
                        await query.message.reply_text("❌ آگهی یا حواله یافت نشد.")
                        return
                    conn.execute(
                        "UPDATE ads SET status = 'approved' WHERE id = ?",
                        (ad_id,),
                    )
                    conn.commit()
                logger.debug(f"Ad {ad_id} approved by admin {user_id}")
                await query.message.reply_text(f"✅آگهی شما با موفقیت تأیید شد.")
                # اطلاع به کاربر
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=(
                        f"✅ آگهی شما تأیید شد و در کانال منتشر شد:\n"
                        f"عنوان: {ad['title']}\n"
                        f"توضیحات: {ad['description']}\n"
                        f"قیمت: {ad['price']} تومان\n"
                        f"📢 برای مشاهده موارد دیگر، از دکمه 'نمایش آگهی‌ها' استفاده کنید."
                    ),
                )
                # انتشار در کانال @bolori_car
                channel_text = (
                    f"📋 {ad_type.capitalize()} جدید:\n"
                    f"عنوان: {ad['title']}\n"
                    f"توضیحات: {ad['description']}\n"
                    f"قیمت: {ad['price']} تومان\n"
                    f"📢 برای جزئیات بیشتر به ربات مراجعه کنید: @Bolori_car_bot"
                )
                if ad['image_id']:
                    await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=ad['image_id'],
                        caption=channel_text,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=channel_text,
                    )
                logger.debug(f"Ad {ad_id} published to channel {CHANNEL_ID}")
            except sqlite3.Error as e:
                logger.error(f"Database error in approve for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در تأیید آگهی یا حواله رخ داد.")
            except TelegramError as e:
                logger.error(f"Telegram error in approve for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در ارسال پیام یا انتشار در کانال رخ داد.")
            except ValueError as e:
                logger.error(f"Invalid callback data format: {callback_data}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطای فرمت داده.")
            except Exception as e:
                logger.error(f"Unexpected error in approve for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در پردازش درخواست رخ داد.")
        else:
            await query.message.reply_text(" ⚠ شما ادمین نیستید.")
    elif callback_data.startswith("reject_"):
        if user_id in ADMIN_ID:
            try:
                _, ad_type, ad_id = callback_data.split("_")
                ad_id = int(ad_id)
                with get_db_connection() as conn:
                    ad = conn.execute(
                        "SELECT user_id FROM ads WHERE id = ?", (ad_id,)
                    ).fetchone()
                    if not ad:
                        logger.error(f"Ad with id {ad_id} not found")
                        await query.message.reply_text("❌ آگهی یا حواله یافت نشد.")
                        return
                    conn.execute(
                        "UPDATE ads SET status = 'rejected' WHERE id = ?",
                        (ad_id,),
                    )
                    conn.commit()
                await query.message.reply_text(f"❌ {ad_type.capitalize()} رد شد.")
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=f"❌ {ad_type.capitalize()} شما رد شد. لطفاً با ادمین تماس بگیرید."
                )
            except sqlite3.Error as e:
                logger.error(f"Database error in reject for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در رد آگهی یا حواله رخ داد.")
            except TelegramError as e:
                logger.error(f"Telegram error in reject for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در ارسال پیام رخ داد.")
            except ValueError as e:
                logger.error(f"Invalid callback data format: {callback_data}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطای فرمت داده.")
            except Exception as e:
                logger.error(f"Unexpected error in reject for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در پردازش درخواست رخ داد.")
        else:
            await query.message.reply_text("⚠️ شما دسترسی ادمین ندارید.")
    else:
        logger.warning(f"Unknown callback data: {callback_data}")
        await query.message.reply_text("⚠️ گزینه ناشناخته.")

# ... (بخش‌های دیگر کد بدون تغییر باقی می‌مانند)

# تابع نمایش آگهی‌ها
async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Show ads requested by user {user_id}")
    try:
        with get_db_connection() as conn:
            ads = conn.execute(
                "SELECT * FROM ads WHERE status = 'approved' ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
        if not ads:
            await update.effective_message.reply_text("📪 هیچ آگهی تأییدشده‌ای یافت نشد.")
            return
        for ad in ads:
            ad_text = (
                f"📋 {ad['type'].capitalize()}: {ad['title']}\n"
                f"توضیحات: {ad['description']}\n"
                f"قیمت: {ad['price']} تومان\n"
                f"تاریخ: {ad['created_at']}"
            )
            if ad['image_id']:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=ad['image_id'],
                    caption=ad_text
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=ad_text
                )
    except sqlite3.Error as e:
        logger.error(f"Database error in show_ads: {e}")
        await update.effective_message.reply_text("❌ خطایی در نمایش آگهی‌ها رخ داد.")
    except TelegramError as e:
        logger.error(f"Telegram error in show_ads: {e}")
        await update.effective_message.reply_text("❌ خطایی در ارسال آگهی‌ها رخ داد.")

# تابع پنل ادمین
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Admin panel requested by user {user_id}")
    if user_id in ADMIN_ID:
        buttons = [
            [InlineKeyboardButton("📋 بررسی آگهی‌ها", callback_data="review_ads")],
            [InlineKeyboardButton("📊 آمار کاربران", callback_data="stats")]
        ]
        await update.effective_message.reply_text(
            "پنل ادمین:\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        logger.debug(f"User {user_id} is not an admin")
        await update.effective_message.reply_text("⚠️ شما دسترسی ادمین ندارید.")

# تابع بررسی آگهی‌ها توسط ادمین
async def review_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Review ads requested by user {user_id}")
    if user_id in ADMIN_ID:
        try:
            with get_db_connection() as conn:
                ads = conn.execute(
                    "SELECT * FROM ads WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
            if not ads:
                await update.effective_message.reply_text("📪 هیچ آگهی در انتظار تأییدی یافت نشد.")
                return
            ad_text = (
                f"📋 {ads['type'].capitalize()}: {ads['title']}\n"
                f"توضیحات: {ads['description']}\n"
                f"قیمت: {ads['price']} تومان\n"
                f"کاربر: {ads['user_id']}"
            )
            buttons = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{ads['type']}_{ads['id']}")],
                [InlineKeyboardButton("❌ رد", callback_data=f"reject_{ads['type']}_{ads['id']}")]
            ]
            if ads['image_id']:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=ads['image_id'],
                    caption=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
        except sqlite3.Error as e:
            logger.error(f"Database error in review_ads: {e}")
            await update.effective_message.reply_text("❌ خطایی در بررسی آگهی‌ها رخ داد.")
        except TelegramError as e:
            logger.error(f"Telegram error in review_ads: {e}")
            await update.effective_message.reply_text("❌ خطایی در ارسال آگهی رخ داد.")

# تابع مدیریت callbackها
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    user_id = update.effective_user.id
    logger.debug(f"Callback received from user {user_id}: {callback_data}")

    if callback_data == "check_membership":
        if await check_membership(update, context):
            await start(update, context)
        else:
            await query.message.reply_text("⚠️ شما هنوز در کانال عضو نشده‌اید.")
    elif callback_data == "post_ad":
        await post_ad_start(update, context)
    elif callback_data == "post_referral":
        await post_referral_start(update, context)
    elif callback_data == "show_ads":
        await show_ads(update, context)
    elif callback_data == "admin_panel":
        await admin_panel(update, context)
    elif callback_data == "stats":
        await stats(update, context)
    elif callback_data == "review_ads":
        await review_ads(update, context)
    elif callback_data.startswith("approve_"):
        if user_id in ADMIN_ID:
            try:
                _, ad_type, ad_id = callback_data.split("_")
                ad_id = int(ad_id)
                with get_db_connection() as conn:
                    ad = conn.execute(
                        "SELECT user_id, title, description, price, image_id FROM ads WHERE id = ?",
                        (ad_id,),
                    ).fetchone()
                    if not ad:
                        logger.error(f"Ad with id {ad_id} not found")
                        await query.message.reply_text("❌ آگهی یافت نشد.")
                        return
                    conn.execute(
                        "UPDATE ads SET status = 'approved' WHERE id = ?",
                        (ad_id,),
                    )
                    conn.commit()
                logger.debug(f"Ad {ad_id} approved by admin {user_id}")
                await query.message.reply_text(f"✅ اگهی شما با موفقیت تأیید شد.")
                # اطلاع به کاربر
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=(
                        f"✅ آگهی شما تأیید شد و در کانال منتشر شد:\n"
                        f"عنوان: {ad['title']}\n"
                        f"توضیحات: {ad['description']}\n"
                        f"قیمت: {ad['price']} تومان\n"
                        f"📢 برای مشاهده آگهی‌های دیگر، از دکمه 'نمایش آگهی‌ها' استفاده کنید."
                    ),
                )
                # انتشار در کانال @bolori_car
                channel_text = (
                    f"📋 آگهی جدید ({ad_type.capitalize()}):\n"
                    f"عنوان: {ad['title']}\n"
                    f"توضیحات: {ad['description']}\n"
                    f"قیمت: {ad['price']} تومان\n"
                    f"📢 برای جزئیات بیشتر به ربات مراجعه کنید: @Bolori_car_bot"
                )
                if ad['image_id']:
                    await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=ad['image_id'],
                        caption=channel_text,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=channel_text,
                    )
                logger.debug(f"Ad {ad_id} published to channel {CHANNEL_ID}")
            except sqlite3.Error as e:
                logger.error(f"Database error in approve for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در تأیید آگهی رخ داد.")
            except TelegramError as e:
                logger.error(f"Telegram error in approve for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در ارسال پیام یا انتشار در کانال رخ داد.")
            except ValueError as e:
                logger.error(f"Invalid callback data format: {callback_data}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطای فرمت داده.")
            except Exception as e:
                logger.error(f"Unexpected error in approve for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در پردازش درخواست رخ داد.")
        else:
            await query.message.reply_text("⚠️شما ادمین نیستید.")
    elif callback_data.startswith("reject_"):
        if user_id in ADMIN_ID:
            try:
                _, ad_type, ad_id = callback_data.split("_")
                ad_id = int(ad_id)
                with get_db_connection() as conn:
                    ad = conn.execute(
                        "SELECT user_id FROM ads WHERE id = ?", (ad_id,)
                    ).fetchone()
                    if not ad:
                        logger.error(f"Ad with id {ad_id} not found")
                        await query.message.reply_text("❌ آگهی یافت نشد.")
                        return
                    conn.execute(
                        "UPDATE ads SET status = 'rejected' WHERE id = ?",
                        (ad_id,),
                    )
                    conn.commit()
                await query.message.reply_text(f"❌ {ad_type.capitalize()} رد شد.")
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=f"❌ شما رد شد. لطفاً با ادمین تماس بگیرید."
                )
            except sqlite3.Error as e:
                logger.error(f"Database error in reject for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در رد آگهی رخ داد.")
            except TelegramError as e:
                logger.error(f"Telegram error in reject for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در ارسال پیام رخ داد.")
            except ValueError as e:
                logger.error(f"Invalid callback data format: {callback_data}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطای فرمت داده.")
            except Exception as e:
                logger.error(f"Unexpected error in reject for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در پردازش درخواست رخ داد.")
        else:
            await query.message.reply_text("⚠️شما دسترسی ادمین نیستید.")
    else:
        logger.warning(f"Unknown callback data: {callback_data}")
        await query.message.reply_text("⚠️ گزینه ناشناخته.")

# تابع مدیریت خطاها
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error processing update {update}: {context.error}", exc_info=context.error)
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید."
            )
        except Exception:
            pass

# تابع دریافت برنامه
def get_application():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, post_ad_handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, post_ad_handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, post_referral_handle_message))
    application.add_error_handler(error_handler)
    return application

# تابع اصلی
async def main():
    logger.debug("Starting main function...")
    try:
        init_db()
        global ADMIN_ID, APPLICATION
        ADMIN_ID = load_admins()
        APPLICATION = get_application()
        await APPLICATION.initialize()
        logger.debug("Application initialized.")
        await APPLICATION.bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET
        )
        logger.debug("Webhook set successfully.")
        await APPLICATION.start()
        logger.debug("Application started.")
        asyncio.create_task(process_update_queue())
        logger.debug("Process update queue task created.")
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
        raise

# تابع راه‌اندازی سرور و ربات
async def run():
    init_db()
    global ADMIN_ID
    ADMIN_ID = load_admins()
    app.router.add_post('/webhook', webhook)
    app.router.add_get('/', health_check)
    await main()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Server started on port {PORT}")

# اجرای برنامه
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run())
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
