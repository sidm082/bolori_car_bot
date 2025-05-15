import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError, Forbidden, BadRequest
from aiohttp import web
import queue
import asyncio
import sqlite3
from datetime import datetime
import time
import os
import json
import re
import InputMediaPhoto
from threading import Lock

# تنظیم لاگ‌گیری
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger('telegram').setLevel(logging.DEBUG)
logging.getLogger('httpcore').setLevel(logging.DEBUG)
logging.getLogger('httpx').setLevel(logging.DEBUG)

# دیکشنری و Lock برای FSM
FSM_STATES = {}
FSM_LOCK = Lock()

# متغیرهای محیطی
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", 8080))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@bolori_car")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/bolori_car")

if not all([BOT_TOKEN, WEBHOOK_URL, CHANNEL_ID, CHANNEL_URL]):
    logger.error("Missing required environment variables")
    raise ValueError("Missing required environment variables")

# متغیرهای جهانی
update_queue = queue.Queue()
app = web.Application()
APPLICATION = None
ADMIN_ID = [5677216420]

# اتصال به دیتابیس
def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

# مقداردهی اولیه دیتابیس
def init_db():
    logger.debug("Initializing database...")
    with get_db_connection() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users
                      (user_id INTEGER PRIMARY KEY, joined TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ads
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
                       title TEXT, description TEXT, price INTEGER, created_at TEXT,
                       status TEXT, image_id TEXT, phone TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS admins
                      (user_id INTEGER PRIMARY KEY)''')
        conn.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (5677216420,))
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_ads_status
                      ON ads (status)''')
        conn.commit()
        logger.debug("Database initialized successfully.")

# بارگذاری ادمین‌ها
def load_admins():
    logger.debug("Loading admin IDs...")
    with get_db_connection() as conn:
        admins = conn.execute('SELECT user_id FROM admins').fetchall()
        admin_ids = [admin['user_id'] for admin in admins]
        logger.debug(f"Loaded {len(admin_ids)} admin IDs")
        return admin_ids

# پردازش ایمن JSON
def safe_json_loads(data):
    if not data:
        return []
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON in image_id: {data}")
        return [data] if data else []

# مسیر Webhook
async def webhook(request):
    logger.debug("Received webhook request")
    if not APPLICATION:
        logger.error("Application is not initialized")
        return web.Response(status=500, text='Application not initialized')
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
    try:
        with get_db_connection() as conn:
            conn.execute("SELECT 1")
        if APPLICATION and APPLICATION.running:
            return web.Response(status=200, text='OK')
        return web.Response(status=503, text='Application not running')
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return web.Response(status=500, text='Internal Server Error')

# پردازش صف آپدیت‌ها
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
            await asyncio.sleep(1)

# بررسی عضویت (غیرفعال برای تست)
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Checking membership for user {user_id} in channel {CHANNEL_ID}")
    logger.debug("Skipping membership check for testing")
    return True

# دستور start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Start command received from user {update.effective_user.id}")
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
            "به ربات رسمی ثبت آگهی و حواله خودرو *اتوگالری بلوری* خوش آمدید. از طریق این ربات می‌توانید:\n"
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
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ عضویت در کانال", url=CHANNEL_URL)],
            [InlineKeyboardButton("🔄 بررسی عضویت", callback_data="check_membership")]
        ])
        await update.effective_message.reply_text(
            "⚠️ برای استفاده از ربات، لطفاً ابتدا در کانال ما عضو شوید:",
            reply_markup=keyboard
        )

# دستور cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with FSM_LOCK:
        if user_id in FSM_STATES:
            del FSM_STATES[user_id]
            await update.message.reply_text("فرآیند لغو شد. برای شروع دوباره، /start را بزنید.")
        else:
            await update.message.reply_text("هیچ فرآیند فعالی وجود ندارد.")

# دستور admin
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

# دستور stats
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

# شروع ثبت آگهی
async def post_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Post ad started for user {user_id}")
    with FSM_LOCK:
        FSM_STATES[user_id] = {"state": "post_ad_title"}
    await update.effective_message.reply_text("لطفاً برند و مدل خودروی خود را وارد نمایید.(مثلاً: فروش پژو207 پانا):")

# شروع ثبت حواله
async def post_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Post referral started for user {user_id}")
    with FSM_LOCK:
        FSM_STATES[user_id] = {"state": "post_referral_title"}
    await update.effective_message.reply_text("لطفاً عنوان حواله را وارد کنید (مثال: حواله پژو 207):")

# مدیریت پیام‌های آگهی
async def post_ad_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with FSM_LOCK:
        if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
            logger.debug(f"No FSM state for user {user_id}, ignoring message")
            return
        state = FSM_STATES[user_id]["state"]
    logger.debug(f"Handling message for user {user_id} in state {state}")

    try:
        if state == "post_ad_title":
            message_text = update.message.text
            with FSM_LOCK:
                FSM_STATES[user_id]["title"] = message_text
                FSM_STATES[user_id]["state"] = "post_ad_description"
            await update.message.reply_text("لطفا * اطلاعات خودرو * شامل رنگ ، کارکرد ، وضعیت بدنه ، وضعیت فنی و غیره را وارد نمایید.")
        elif state == "post_ad_description":
            message_text = update.message.text
            with FSM_LOCK:
                FSM_STATES[user_id]["description"] = message_text
                FSM_STATES[user_id]["state"] = "post_ad_price"
            await update.message.reply_text("*لطفاً قیمت آگهی را به تومان وارد کنید *(فقط عدد):")
        elif state == "post_ad_price":
            message_text = update.message.text
            try:
                price = int(message_text)
                with FSM_LOCK:
                    FSM_STATES[user_id]["price"] = price
                    FSM_STATES[user_id]["state"] = "post_ad_phone"
                await update.message.reply_text("لطفاً شماره تلفن خود را وارد کنید :")
            except ValueError:
                await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
        elif state == "post_ad_phone":
            message_text = update.message.text
            if re.match(r"^(09|\+98)\d{9}$", message_text):
                with FSM_LOCK:
                    FSM_STATES[user_id]["phone"] = message_text
                    FSM_STATES[user_id]["state"] = "post_ad_image"
                    FSM_STATES[user_id]["images"] = []
                await update.message.reply_text("اکنون لطفاً تصاویر واضح از خودرو ارسال نمایید (حداکثر 5 عدد). پس از ارسال همه عکس‌ها، /done را بزنید.")
            else:
                await update.message.reply_text(
                    "⚠️ شماره تلفن باید با 09 یا +98 شروع شود و 11 یا 12 رقم باشد (مثال: 09123456789 یا +989123456789). لطفاً دوباره وارد کنید:"
                )
        elif state == "post_ad_image":
            if update.message.text and update.message.text == "/done":
                with FSM_LOCK:
                    if not FSM_STATES[user_id]["images"]:
                        await update.message.reply_text("⚠️ هیچ عکسی ارسال نکرده‌اید. لطفاً حداقل یک عکس ارسال کنید.")
                        return
                    else:
                        await save_ad(update, context)
            elif update.message.photo:
                with FSM_LOCK:
                    if len(FSM_STATES[user_id]["images"]) >= 5:
                        await update.message.reply_text("⚠️ شما حداکثر تعداد عکس (5 عدد) را ارسال کرده‌اید. برای ادامه /done را بزنید.")
                        return
                    photo = update.message.photo[-1].file_id
                    FSM_STATES[user_id]["images"].append(photo)
                    count = len(FSM_STATES[user_id]["images"])
                await update.message.reply_text(f"عکس {count} از 5 ذخیره شد. برای ادامه عکس دیگری بفرستید یا /done را بزنید.")
            else:
                await update.message.reply_text("لطفاً فقط عکس ارسال کنید یا برای اتمام /done را بزنید.")
    except Exception as e:
        logger.error(f"Error in post_ad_handle_message for user {user_id}: {e}", exc_info=True)
        await update.effective_message.reply_text("❌ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید.")

# ذخیره آگهی
async def save_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Saving ad for user {user_id}")
    try:
        with get_db_connection() as conn:
            with FSM_LOCK:
                images_json = json.dumps(FSM_STATES[user_id].get("images", []))
            cursor = conn.execute(
                '''INSERT INTO ads (user_id, type, title, description, price, created_at, status, image_id, phone)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    user_id,
                    "ad",
                    FSM_STATES[user_id]["title"],
                    FSM_STATES[user_id]["description"],
                    FSM_STATES[user_id]["price"],
                    datetime.now().isoformat(),
                    "pending",
                    images_json,
                    FSM_STATES[user_id]["phone"],
                ),
            )
            ad_id = cursor.lastrowid
            conn.commit()
        
        logger.debug(f"Ad saved successfully for user {user_id} with ad_id {ad_id}")
        await update.message.reply_text("✅ آگهی شما با موفقیت ثبت شد و پس از بررسی، در کانال منتشر خواهد شد.")
        
        username = update.effective_user.username or "بدون نام کاربری"
        with FSM_LOCK:
            images = FSM_STATES[user_id].get("images", [])
        
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
                f"قیمت: {FSM_STATES[user_id]['price']:,} تومان\n"
                f"تعداد عکس‌ها: {len(images)}"
            )
            
            if images:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=images[0],
                    caption=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                for photo in images[1:]:
                    await context.bot.send_photo(chat_id=admin_id, photo=photo)
                    await asyncio.sleep(0.5)
            else:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            await asyncio.sleep(1)
        
        with FSM_LOCK:
            del FSM_STATES[user_id]
    except Exception as e:
        logger.error(f"Error in save_ad: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ خطایی در ثبت آگهی رخ داد.")

# مدیریت پیام‌های حواله
async def post_referral_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Entering post_referral_handle_message for user {user_id}")
    
    with FSM_LOCK:
        if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
            logger.debug(f"No FSM state for user {user_id}, ignoring message")
            try:
                await update.message.reply_text("⚠️ لطفاً فرآیند ثبت حواله را از ابتدا شروع کنید (/start).")
            except Exception as e:
                logger.error(f"Failed to send invalid state message to user {user_id}: {e}", exc_info=True)
            return
        state = FSM_STATES[user_id]["state"]
    message_text = update.message.text
    logger.debug(f"Handling message for user {user_id} in state {state}: {message_text}")
    try:
        if state == "post_referral_title":
            with FSM_LOCK:
                FSM_STATES[user_id]["title"] = message_text
                FSM_STATES[user_id]["state"] = "post_referral_description"
            await update.message.reply_text("لطفاً توضیحات حواله را وارد کنید:")
        elif state == "post_referral_description":
            with FSM_LOCK:
                FSM_STATES[user_id]["description"] = message_text
                FSM_STATES[user_id]["state"] = "post_referral_price"
            await update.message.reply_text("لطفاً قیمت حواله را به تومان وارد کنید (فقط عدد):")
        elif state == "post_referral_price":
            try:
                price = int(message_text)
                with FSM_LOCK:
                    FSM_STATES[user_id]["price"] = price
                    FSM_STATES[user_id]["state"] = "post_referral_phone"
                await update.message.reply_text(
                    "لطفاً شماره تلفن خود را وارد کنید (با شروع 09 یا +98، مثال: 09123456789 یا +989123456789):"
                )
            except ValueError:
                await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
        elif state == "post_referral_phone":
            if re.match(r"^(09|\+98)\d{9}$", message_text):
                with FSM_LOCK:
                    FSM_STATES[user_id]["phone"] = message_text
                await save_referral(update, context)
            else:
                await update.message.reply_text(
                    "⚠️ شماره تلفن باید با 09 یا +98 شروع شود و 11 یا 12 رقم باشد (مثال: 09123456789 یا +989123456789). لطفاً دوباره وارد کنید:"
                )
    except Exception as e:
        logger.error(f"Error in post_referral_handle_message for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در پردازش درخواست شما رخ داد.")
        with FSM_LOCK:
            if user_id in FSM_STATES:
                del FSM_STATES[user_id]

# ذخیره حواله
async def save_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Saving referral for user {user_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                '''INSERT INTO ads (user_id, type, title, description, price, created_at, status, image_id, phone)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    user_id,
                    "referral",
                    FSM_STATES[user_id]["title"],
                    FSM_STATES[user_id]["description"],
                    FSM_STATES[user_id]["price"],
                    datetime.now().isoformat(),
                    "pending",
                    None,
                    FSM_STATES[user_id]["phone"],
                ),
            )
            ad_id = cursor.lastrowid
            conn.commit()
        logger.debug(f"Referral saved successfully for user {user_id} with ad_id {ad_id}")
        await update.message.reply_text("🌟 حواله شما ثبت شد و در انتظار تأیید ادمین است.\n*ممنون از اعتماد شما*")
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
            await asyncio.sleep(1)
        with FSM_LOCK:
            del FSM_STATES[user_id]
    except Exception as e:
        logger.error(f"Error in save_referral: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ خطایی در ثبت حواله رخ داد.")
# نمایش آگهی‌ها
async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        with get_db_connection() as conn:
            ads = conn.execute(
                "SELECT * FROM ads WHERE status = 'approved' ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
        
        if not ads:
            await update.effective_message.reply_text("📭 هیچ آگهی فعالی موجود نیست.")
            return
            
        for ad in ads:
            images = safe_json_loads(ad['image_id'])
            ad_text = (
                f"🚗 {ad['title']}\n"
                f"📝 توضیحات: {ad['description']}\n"
                f"💰 قیمت: {ad['price']:,} تومان\n"
                f"""➖➖➖➖➖
☑️ اتوگالــری بلـــوری
▫️خرید▫️فروش▫️کارشناسی
+989153632957
➖➖➖➖
@Bolori_Car
جهت ثبت آگهی تان به ربات زیر مراجعه کنید.
@bolori_car_bot"""
            )
            
            if images:
                # ایجاد لیست رسانه‌ها برای MediaGroup
                media = [
                    telegram.InputMediaPhoto(media=photo, caption=ad_text if i == 0 else None)
                    for i, photo in enumerate(images)
                ]
                await context.bot.send_media_group(
                    chat_id=user_id,
                    media=media
                )
            else:
                await context.bot.send_message(chat_id=user_id, text=ad_text)
            await asyncio.sleep(1)  # تأخیر بین آگهی‌ها برای جلوگیری از اسپم
            
    except Exception as e:
        logger.error(f"Error showing ads: {str(e)}", exc_info=True)
        await update.effective_message.reply_text("❌ خطایی در نمایش آگهی‌ها رخ داد.")

# پنل ادمین
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

# بررسی آگهی‌ها
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
            images = safe_json_loads(ads['image_id'])
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
            if images:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=images[0],
                    caption=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                for photo in images[1:]:
                    await context.bot.send_photo(chat_id=user_id, photo=photo)
                    await asyncio.sleep(0.5)
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=ad_text,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
        except Exception as e:
            logger.error(f"Error in review_ads: {str(e)}", exc_info=True)
            await update.effective_message.reply_text("❌ خطایی در بررسی آگهی‌ها رخ داد.")

# دیسپچر پیام‌ها (اصلاح‌شده برای جلوگیری از AttributeError)
async def message_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Message dispatcher for user {user_id}: {update.message.text if update.message and update.message.text else 'Non-text message'}")
    
    if not update.message:
        logger.warning(f"Received update without message: {update.to_dict()}")
        return  # نادیده گرفتن آپدیت‌های بدون پیام
    
    with FSM_LOCK:
        if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
            logger.debug(f"No FSM state for user {user_id}, prompting to start")
            await update.message.reply_text("لطفاً فرآیند ثبت آگهی یا حواله را با زدن دکمه‌های مربوطه شروع کنید.")
            return
        state = FSM_STATES[user_id]["state"]
    logger.debug(f"User {user_id} is in state {state}")

    if state.startswith("post_ad"):
        await post_ad_handle_message(update, context)
    elif state.startswith("post_referral"):
        await post_referral_handle_message(update, context)
    else:
        logger.debug(f"Invalid state for user {user_id}: {state}")
        await update.message.reply_text("⚠️ حالت نامعتبر. لطفاً دوباره فرآیند را شروع کنید.")
        with FSM_LOCK:
            if user_id in FSM_STATES:
                del FSM_STATES[user_id]

# مدیریت Callback
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
                        "SELECT user_id, title, description, price, image_id, phone FROM ads WHERE id = ?",
                        (ad_id,),
                    ).fetchone()
                    if not ad:
                        logger.error(f"Ad with id {ad_id} not found")
                        await query.message.reply_text("❌ آگهی یافت نشد.")
                        return
                    
                    images = safe_json_loads(ad['image_id'])
                    
                    conn.execute(
                        "UPDATE ads SET status = 'approved' WHERE id = ?",
                        (ad_id,),
                    )
                    conn.commit()
                
                logger.debug(f"Ad {ad_id} approved by admin {user_id}")
                await query.message.reply_text(f"✅ آگهی/حواله با موفقیت تأیید شد.")
                
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=(
                        f"✅ {ad_type.capitalize()} شما تأیید شد و در کانال منتشر شد:\n"
                        f"عنوان: {ad['title']}\n"
                        f"توضیحات: {ad['description']}\n"
                        f"قیمت: {ad['price']:,} تومان\n\n"
                        f"📢 برای مشاهده آگهی‌های دیگر، از دکمه 'نمایش آگهی‌ها' استفاده کنید."
                    ),
                )
                
                channel_text = (
                    f"🚗 آگهی جدید:\n"
                    f"عنوان: {ad['title']}\n"
                    f"توضیحات: {ad['description']}\n"
                    f"قیمت: {ad['price']:,} تومان\n"
                    f"📢 برای جزئیات بیشتر به ربات مراجعه کنید: @Bolori_car_bot\n"
                    f"""➖➖➖➖➖
☑️ اتوگالــری بلـــوری
▫️خرید▫️فروش▫️کارشناسی
+989153632957
➖➖➖➖
@Bolori_Car
جهت ثبت آگهی تان به ربات زیر مراجعه کنید.
@bolori_car_bot"""
                )
                
                if images:
                    await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=images[0],
                        caption=channel_text,
                    )
                    for photo in images[1:]:
                        await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo)
                        await asyncio.sleep(0.5)
                else:
                    await context.bot.send_message(chat_id=CHANNEL_ID, text=channel_text)
                
                logger.debug(f"Ad {ad_id} published to channel {CHANNEL_ID}")
                
            except Exception as e:
                logger.error(f"Error in approve for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در تأیید آگهی رخ داد.")
        else:
            await query.message.reply_text("⚠️ شما ادمین نیستید.")
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
                    text=f"❌ {ad_type.capitalize()} شما رد شد. لطفاً با ادمین تماس بگیرید."
                )
            except Exception as e:
                logger.error(f"Error in reject for ad {ad_id}: {e}", exc_info=True)
                await query.message.reply_text("❌ خطایی در رد آگهی رخ داد.")
        else:
            await query.message.reply_text("⚠️ شما ادمین نیستید.")
    else:
        logger.warning(f"Unknown callback data: {callback_data}")
        await query.message.reply_text("⚠️ گزینه ناشناخته.")

# مدیریت خطاها
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ خطایی در پردازش درخواست شما رخ داد. لطفاً دوباره تلاش کنید."
            )
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}", exc_info=True)

# ساخت اپلیکیشن
def get_application():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("done", post_ad_handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO | filters.COMMAND,
        message_dispatcher
    ))
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
        await APPLICATION.bot.delete_webhook(drop_pending_updates=True)
        logger.debug("Webhook deleted.")
        await APPLICATION.bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None
        )
        logger.debug("Webhook set successfully.")
        await APPLICATION.start()
        logger.debug("Application started.")
        asyncio.create_task(process_update_queue())
        logger.debug("Process update queue task created.")
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
        raise

# تابع اجرا
async def run():
    init_db()
    global ADMIN_ID, APPLICATION
    ADMIN_ID = load_admins()
    app.router.add_post('/webhook', webhook)
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Server started on port {PORT}")
    try:
        await main()
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if APPLICATION:
            await APPLICATION.bot.delete_webhook(drop_pending_updates=True)
            await APPLICATION.stop()
        await runner.cleanup()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run())
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
