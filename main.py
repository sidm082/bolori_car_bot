from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from datetime import datetime
import sqlite3
import os
from contextlib import closing
import logging
from threading import Thread
from fastapi import FastAPI
import uvicorn

# تنظیم لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تنظیمات اولیه
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set")
ADMIN_ID = 5677216420  # جایگزین با آی دی ادمین واقعی
DATABASE_PATH = os.path.join(os.getcwd(), 'ads.db')

# تعریف مراحل ConversationHandler
(START, TITLE, DESCRIPTION, PRICE, PHOTO, CONFIRM, PHONE) = range(7)

# متغیرهای جهانی
users = set()
approved_ads = []

# --- توابع پایگاه داده ---
def init_db():
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    title TEXT,
                    description TEXT,
                    price TEXT,
                    photo TEXT,
                    approved INTEGER DEFAULT 0,
                    contact TEXT,
                    date TEXT
                )
            ''')
            conn.commit()
            logger.info("✅ جدول ads ساخته شد یا به‌روز شد.")
    except Exception as e:
        logger.error(f"❌ خطا در ساخت جدول: {e}")

def load_ads():
    logger.info("🔄 در حال بارگذاری آگهی‌های تایید شده از دیتابیس...")
    approved_ads = []
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM ads WHERE approved = 1')
            for row in cursor.fetchall():
                approved_ads.append({
                    'title': row[3],
                    'description': row[4],
                    'price': row[5],
                    'photo': row[6],
                    'phone': row[8],
                    'username': row[2],
                    'user_id': row[1],
                    'date': datetime.fromisoformat(row[9]) if row[9] else datetime.now()
                })
    except Exception as e:
        logger.error(f"خطا در بارگذاری آگهی‌ها: {e}")
    return approved_ads

def save_ad(ad, approved=False):
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO ads (title, description, price, photo, contact, username, user_id, date, approved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ad['title'], ad['description'], ad['price'], ad['photo'], ad['phone'],
                ad['username'], ad['user_id'], ad['date'].isoformat(), approved
            ))
            conn.commit()
            cursor.execute('SELECT last_insert_rowid()')
            return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"خطا در ذخیره آگهی: {e}")
        return None

# --- توابع وب سرور برای Render ---
def run_web_server():
    app = FastAPI()
    
    @app.get("/")
    def home():
        return {"status": "Bot is running"}
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        log_level="error"
    )

# --- توابع هندلر ربات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_user.id)
    keyboard = [
        [KeyboardButton("📝 ثبت آگهی")],
        [KeyboardButton("📋 تمامی آگهی‌ها")],
        [KeyboardButton("🔍 کمترین قیمت"), KeyboardButton("🔍 بیشترین قیمت")],
        [KeyboardButton("🆕 جدیدترین"), KeyboardButton("🕰 قدیمی‌ترین")],
        [KeyboardButton("🔔 یادآوری آگهی‌های تایید نشده")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=reply_markup)
    return START

# تابع مدیریت انتخاب‌های اولیه
async def handle_start_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📝 ثبت آگهی":
        await update.message.reply_text("لطفاً عنوان آگهی را وارد کنید (حداکثر 100 کاراکتر):")
        return TITLE
    elif text == "📋 تمامی آگهی‌ها":
        if not approved_ads:
            await update.message.reply_text("هنوز هیچ آگهی تایید شده‌ای وجود ندارد.")
        else:
            for ad in approved_ads:
                caption = f"📢 آگهی تایید شده\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}\n👤 ارسال‌کننده: {ad['username']}"
                try:
                    await update.message.reply_photo(photo=ad['photo'], caption=caption)
                except Exception as e:
                    logger.error(f"خطا در ارسال آگهی: {e}")
                    continue
        return START
    elif text == "🔔 یادآوری آگهی‌های تایید نشده":
        try:
            with closing(sqlite3.connect(DATABASE_PATH)) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM ads WHERE approved = 0 AND user_id = ?', (update.effective_user.id,))
                unapproved_ads = cursor.fetchall()
                if not unapproved_ads:
                    await update.message.reply_text("هیچ آگهی تایید نشده‌ای وجود ندارد.")
                else:
                    await update.message.reply_text(f"شما {len(unapproved_ads)} آگهی تایید نشده دارید.")
        except Exception as e:
            logger.error(f"خطا در بررسی آگهی‌های تایید نشده: {e}")
            await update.message.reply_text("خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        return START
    elif text in ["🔍 کمترین قیمت", "🔍 بیشترین قیمت", "🆕 جدیدترین", "🕰 قدیمی‌ترین"]:
        command = {
            "🔍 کمترین قیمت": "/lowest",
            "🔍 بیشترین قیمت": "/highest",
            "🆕 جدیدترین": "/newest",
            "🕰 قدیمی‌ترین": "/oldest"
        }[text]
        update.message.text = command
        await send_filtered_ads(update, context)
        return START
    else:
        await update.message.reply_text("گزینه نامعتبر است. لطفاً از دکمه‌ها استفاده کنید.")
        return START

# تابع دریافت عنوان
async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title or len(title) > 100:
        await update.message.reply_text("عنوان نامعتبر است. لطفاً عنوانی بین 1 تا 100 کاراکتر وارد کنید:")
        return TITLE
    context.user_data['title'] = title
    await update.message.reply_text("توضیحات آگهی را وارد کنید (حداکثر 500 کاراکتر):")
    return DESCRIPTION

# تابع دریافت توضیحات
async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description or len(description) > 500:
        await update.message.reply_text("توضیحات نامعتبر است. لطفاً توضیحاتی بین 1 تا 500 کاراکتر وارد کنید:")
        return DESCRIPTION
    context.user_data['description'] = description
    await update.message.reply_text("قیمت آگهی را وارد کنید (فقط عدد):")
    return PRICE

# تابع دریافت قیمت
async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = update.message.text.strip()
    try:
        float(price)  # بررسی اینکه قیمت یک عدد معتبر است
        context.user_data['price'] = price
        await update.message.reply_text("یک عکس برای آگهی ارسال کنید (حداکثر 10 مگابایت):")
        return PHOTO
    except ValueError:
        await update.message.reply_text("لطفاً یک قیمت معتبر (عدد) وارد کنید:")
        return PRICE

# تابع دریافت عکس
async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo_file = update.message.photo[-1].file_id
        context.user_data['photo'] = photo_file
        button = KeyboardButton("📞 ارسال شماره تماس", request_contact=True)
        reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "لطفاً شماره تماس خود را وارد کنید. توجه داشته باشید که شماره شما فقط برای ادمین قابل رویت است.",
            reply_markup=reply_markup
        )
        return PHONE
    except Exception as e:
        logger.error(f"خطا در دریافت عکس: {e}")
        await update.message.reply_text("لطفاً یک عکس معتبر ارسال کنید.")
        return PHOTO

# تابع دریافت شماره تماس
async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        contact = update.message.contact
        context.user_data['phone'] = contact.phone_number
        keyboard = [[InlineKeyboardButton("تأیید و ارسال به ادمین", callback_data="confirm")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("آگهی شما آماده است. برای تأیید نهایی کلیک کنید:", reply_markup=reply_markup)
        return CONFIRM
    except Exception as e:
        logger.error(f"خطا در دریافت شماره تماس: {e}")
        await update.message.reply_text("لطفاً شماره تماس معتبر ارسال کنید.")
        return PHONE

# تابع تأیید آگهی
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_data = context.user_data
    user = query.from_user
    username = f"@{user.username}" if user.username else f"ID: {user.id}"

    ad = {
        'title': user_data['title'],
        'description': user_data['description'],
        'price': user_data['price'],
        'photo': user_data['photo'],
        'phone': user_data['phone'],
        'username': username,
        'user_id': user.id,
        'date': datetime.now()
    }

    ad_id = save_ad(ad)
    if not ad_id:
        await query.edit_message_text("خطایی در ذخیره آگهی رخ داد. لطفاً دوباره تلاش کنید.")
        return ConversationHandler.END

    admin_buttons = [[InlineKeyboardButton("✅ تایید آگهی", callback_data=f"approve_{ad_id}")]]
    admin_markup = InlineKeyboardMarkup(admin_buttons)

    caption = (
        f"📢 آگهی جدید برای تأیید\n"
        f"📝 عنوان: {ad['title']}\n"
        f"📄 توضیحات: {ad['description']}\n"
        f"💰 قیمت: {ad['price']}\n"
        f"👤 ارسال‌کننده: {ad['username']}\n"
        f"📞 شماره تماس: {ad['phone']}"
    )
    
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=ad['photo'],
            caption=caption,
            reply_markup=admin_markup
        )
        await query.edit_message_text("آگهی شما با موفقیت ثبت شد و برای تأیید به ادمین ارسال شد.")
    except Exception as e:
        logger.error(f"خطا در ارسال آگهی به ادمین: {e}")
        await query.edit_message_text("خطایی در ارسال آگهی به ادمین رخ داد. لطفاً دوباره تلاش کنید.")
    return ConversationHandler.END

# تابع نمایش آگهی‌ها
async def send_filtered_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text
    if command == "/lowest":
        ads = sorted(approved_ads, key=lambda ad: float(ad['price']))
    elif command == "/highest":
        ads = sorted(approved_ads, key=lambda ad: float(ad['price']), reverse=True)
    elif command == "/newest":
        ads = sorted(approved_ads, key=lambda ad: ad['date'], reverse=True)
    elif command == "/oldest":
        ads = sorted(approved_ads, key=lambda ad: ad['date'])
    
    if not ads:
        await update.message.reply_text("هیچ آگهی تایید شده‌ای موجود نیست.")
    else:
        for ad in ads:
            caption = f"📢 آگهی\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}\n👤 ارسال‌کننده: {ad['username']}"
            try:
                await update.message.reply_photo(photo=ad['photo'], caption=caption)
            except Exception as e:
                logger.error(f"خطا در ارسال آگهی: {e}")
                continue

# --- دستور ربات ---
def main():
    init_db()
    global approved_ads
    approved_ads = load_ads()

    application = Application.builder().token(TOKEN).build()

    # اجرای وب سرور در یک رشته جداگانه
    Thread(target=run_web_server).start()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_choice)],
        states={
            START: [MessageHandler(filters.TEXT, handle_start_choice)],
            TITLE: [MessageHandler(filters.TEXT, get_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT, get_description)],
            PRICE: [MessageHandler(filters.TEXT, get_price)],
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            PHONE: [MessageHandler(filters.CONTACT, get_phone)],
            CONFIRM: [CallbackQueryHandler(confirm, pattern="^confirm$")]
        },
        fallbacks=[],
    )
    application.add_handler(conv_handler)
    
    application.run_polling()

if __name__ == "__main__":
    main()
