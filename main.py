from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
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

# تابع اصلی
def main() -> None:
    # اجرای وب سرور در پس‌زمینه
    Thread(target=run_web_server, daemon=True).start()
    
    init_db()
    approved_ads.extend(load_ads())

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_choice)],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            PHONE: [MessageHandler(filters.CONTACT, get_phone)],
            CONFIRM: [CallbackQueryHandler(confirm, pattern="^confirm$")]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=True
    )

    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(approve, pattern=r"^approve_"))
    application.add_handler(CommandHandler("send", send_message_to_user))
    application.add_handler(CommandHandler(
        ["lowest", "highest", "newest", "oldest"], 
        filter_ads,
        filters=filters.ChatType.PRIVATE
    ))

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()
