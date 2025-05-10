import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import TelegramError
from aiohttp import web
from queue import Queue
import asyncio
import sqlite3
from datetime import datetime
import time
import os
import json

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
update_queue = Queue()
app = web.Application()
ADMIN_ID = [5677216420]  # این مقدار از دیتابیس بارگذاری می‌شود
FSM_STATES = {}  # دیکشنری برای مدیریت وضعیت‌های FSM

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
    application = get_application()
    while True:
        try:
            json_data = update_queue.get_nowait()
            start_time = time.time()
            logger.debug(f"Processing update: {json_data}")
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

# تابع بررسی عضویت (برای تست موقتاً غیرفعال شده)
async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Checking membership for user {user_id} in channel {CHANNEL_ID}")
    # برای تست، بررسی عضویت غیرفعال شده است
    logger.debug("Skipping membership check for testing")
    return True
    # کد اصلی (بعد از اطمینان از دسترسی ربات، این را فعال کنید):
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            logger.debug(f"Membership status for user {user_id}: {member.status}")
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
    """

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
    await update.effective_message.reply_text("لطفاً عنوان آگهی را وارد کنید (مثلاً: فروش پژو 207):")

async def post_ad_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
        return
    state = FSM_STATES[user_id]["state"]
    message_text = update.message.text

    if state == "post_ad_title":
        FSM_STATES[user_id]["title"] = message_text
        FSM_STATES[user_id]["state"] = "post_ad_description"
        await update.message.reply_text("لطفاً توضیحات آگهی را وارد کنید:")
    elif state == "post_ad_description":
        FSM_STATES[user_id]["description"] = message_text
        FSM_STATES[user_id]["state"] = "post_ad_price"
        await update.message.reply_text("لطفاً قیمت آگهی را به تومان وارد کنید (فقط عدد):")
    elif state == "post_ad_price":
        try:
            price = int(message_text)
            FSM_STATES[user_id]["price"] = price
            FSM_STATES[user_id]["state"] = "post_ad_image"
            await update.message.reply_text("لطفاً تصویر آگهی را ارسال کنید (یا /skip برای رد کردن):")
        except ValueError:
            await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
    elif state == "post_ad_image":
        if update.message.text == "/skip":
            FSM_STATES[user_id]["image_id"] = None
            await save_ad(update, context)
        elif update.message.photo:
            FSM_STATES[user_id]["image_id"] = update.message.photo[-1].file_id
            await save_ad(update, context)
        else:
            await update exposé_message.reply_text("لطفاً یک تصویر ارسال کنید یا /skip را بزنید:")

async def save_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Saving ad for user {user_id}")
    try:
        with get_db_connection() as conn:
            conn.execute(
                '''INSERT INTO ads (user_id, type, title, description, price, created_at, status, image_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, "ad", FSM_STATES[user_id]["title"], FSM_STATES[user_id]["description"],
                 FSM_STATES[user_id]["price"], datetime.now().isoformat(), "pending",
                 FSM_STATES[user_id].get("image_id"))
            )
            conn.commit()
        await update.message.reply_text("✅ آگهی شما ثبت شد و در انتظار تأیید ادمین است.")
        # اطلاع به ادمین‌ها
        for admin_id in ADMIN_ID:
            buttons = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_ad_{user_id}")],
                [InlineKeyboardButton("❌ رد", callback_data=f"reject_ad_{user_id}")]
            ]
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"آگهی جدید از کاربر {user_id}:\nعنوان: {FSM_STATES[user_id]['title']}\nتوضیحات: {FSM_STATES[user_id]['description']}\nقیمت: {FSM_STATES[user_id]['price']} تومان",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        del FSM_STATES[user_id]
    except sqlite3.Error as e:
        logger.error(f"Database error in save_ad: {e}")
        await update.message.reply_text("❌ خطایی در ثبت آگهی رخ داد.")

# تابع ثبت حواله
async def post_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Post referral started for user {user_id}")
    FSM_STATES[user_id] = {"state": "post_referral_title"}
    await update.effective_message.reply_text("لطفاً عنوان حواله را وارد کنید (مثلاً: حواله سایپا):")

async def post_referral_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in FSM_STATES or "state" not in FSM_STATES[user_id]:
        return
    state = FSM_STATES[user_id]["state"]
    message_text = update.message.text

    if state == "post_referral_title":
        FSM_STATES[user_id]["title"] = message_text
        FSM_STATES[user_id]["state"] = "post_referral_description"
        await update.message.reply_text("لطفاً توضیحات حواله را وارد کنید:")
    elif state == "post_referral_description":
        FSM_STATES[user_id]["description"] = message_text
        FSM_STATES[user_id]["state"] = "post_referral_price"
        await update.message.reply_text("لطفاً قیمت حواله را به تومان وارد کنید (فقط عدد):")
    elif state == "post_referral_price":
        try:
            price = int(message_text)
            FSM_STATES[user_id]["price"] = price
            await save_referral(update, context)
        except ValueError:
            await update.message.reply_text("لطفاً فقط عدد وارد کنید:")

async def save_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Saving referral for user {user_id}")
    try:
        with get_db_connection() as conn:
            conn.execute(
                '''INSERT INTO ads (user_id, type, title, description, price, created_at, status, image_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, "referral", FSM_STATES[user_id]["title"], FSM_STATES[user_id]["description"],
                 FSM_STATES[user_id]["price"], datetime.now().isoformat(), "pending", None)
            )
            conn.commit()
        await update.message.reply_text("✅ حواله شما ثبت شد و در انتظار تأیید ادمین است.")
        # اطلاع به ادمین‌ها
        for admin_id in ADMIN_ID:
            buttons = [
                [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_referral_{user_id}")],
                [InlineKeyboardButton("❌ رد", callback_data=f"reject_referral_{user_id}")]
            ]
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"حواله جدید از کاربر {user_id}:\nعنوان: {FSM_STATES[user_id]['title']}\nتوضیحات: {FSM_STATES[user_id]['description']}\nقیمت: {FSM_STATES[user_id]['price']} تومان",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        del FSM_STATES[user_id]
    except sqlite3.Error as e:
        logger.error(f"Database error in save_referral: {e}")
        await update.message.reply_text("❌ خطایی در ثبت حواله رخ داد.")

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
    else:
        logger.debug(f"User {user_id} is not an admin")
        await update.effective_message.reply_text("⚠️ شما دسترسی ادمین ندارید.")

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
            _, ad_type, ad_id = callback_data.split("_")
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE ads SET status = 'approved' WHERE id = ?",
                        (ad_id,)
                    )
                    conn.commit()
                await query.message.reply_text(f"✅ {ad_type.capitalize()} با موفقیت تأیید شد.")
                # اطلاع به کاربر
                ad = conn.execute("SELECT user_id FROM ads WHERE id = ?", (ad_id,)).fetchone()
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=f"✅ {ad_type.capitalize()} شما تأیید شد و در کانال نمایش داده خواهد شد."
                )
            except sqlite3.Error as e:
                logger.error(f"Database error in approve: {e}")
                await query.message.reply_text("❌ خطایی در تأیید آگهی رخ داد.")
        else:
            await query.message.reply_text("⚠️ شما دسترسی ادمین ندارید.")
    elif callback_data.startswith("reject_"):
        if user_id in ADMIN_ID:
            _, ad_type, ad_id = callback_data.split("_")
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        "UPDATE ads SET status = 'rejected' WHERE id = ?",
                        (ad_id,)
                    )
                    conn.commit()
                await query.message.reply_text(f"❌ {ad_type.capitalize()} رد شد.")
                # اطلاع به کاربر
                ad = conn.execute("SELECT user_id FROM ads WHERE id = ?", (ad_id,)).fetchone()
                await context.bot.send_message(
                    chat_id=ad['user_id'],
                    text=f"❌ {ad_type.capitalize()} شما رد شد. لطفاً با ادمین تماس بگیرید."
                )
            except sqlite3.Error as e:
                logger.error(f"Database error in reject: {e}")
                await query.message.reply_text("❌ خطایی در رد آگهی رخ داد.")
        else:
            await query.message.reply_text("⚠️ شما دسترسی ادمین ندارید.")
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
    # ثبت هندلرها
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
        global ADMIN_ID
        ADMIN_ID = load_admins()
        application = get_application()
        await application.initialize()
        logger.debug("Application initialized.")
        await application.bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET
        )
        logger.debug("Webhook set successfully.")
        await application.start()
        logger.debug("Application started.")
        asyncio.create_task(process_update_queue())
        logger.debug("Process update queue task created.")
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
        raise

# اجرای برنامه
if __name__ == '__main__':
    init_db()
    ADMIN_ID = load_admins()
    # ثبت مسیرهای aiohttp
    app.router.add_post('/webhook', webhook)
    app.router.add_get('/', health_check)
    # اجرای سرور aiohttp و تابع اصلی
    asyncio.run(main())
    web.run_app(app, host="0.0.0.0", port=PORT)
