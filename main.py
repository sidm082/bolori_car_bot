import os
import logging
import sqlite3
from contextlib import closing
from threading import Thread
from datetime import datetime
from fastapi import FastAPI
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters
from telegram.ext import ContextTypes
from dotenv import load_dotenv

# تنظیم Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# بارگذاری متغیرهای محیطی
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "5677216420").split(",")))
DATABASE_PATH = "ads.db"

if not TOKEN or not ADMIN_IDS:
    raise ValueError("BOT_TOKEN or ADMIN_IDS not set")

# مراحل ConversationHandler
START, TITLE, DESCRIPTION, PRICE, PHOTO, PHONE, CONFIRM = range(7)

# تنظیم وب‌سرور
app = FastAPI()

@app.get("/")
def home():
    return {"status": "Bot is running"}

def run_web_server():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# تنظیم پایگاه داده
def init_db():
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

# هندلرها
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 ثبت آگهی"), KeyboardButton("📋 تمامی آگهی‌ها")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("یکی از گزینه‌ها را انتخاب کنید:", reply_markup=reply_markup)
    return START

async def handle_start_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📝 ثبت آگهی":
        await update.message.reply_text("عنوان آگهی را وارد کنید:")
        return TITLE
    elif text == "📋 تمامی آگهی‌ها":
        ads = load_ads()
        if not ads:
            await update.message.reply_text("هیچ آگهی‌ای موجود نیست.")
        else:
            for ad in ads:
                caption = f"📝 {ad['title']}\n📄 {ad['description']}\n💰 {ad['price']}"
                await update.message.reply_photo(photo=ad['photo'], caption=caption)
        return START
    return START

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title or len(title) > 100:
        await update.message.reply_text("عنوان باید بین 1 تا 100 کاراکتر باشد:")
        return TITLE
    context.user_data['title'] = title
    await update.message.reply_text("توضیحات آگهی را وارد کنید:")
    return DESCRIPTION

# سایر هندلرها مشابه بهبود می‌یابند...

def main():
    init_db()
    Thread(target=run_web_server).start()
    
    application = Application.builder().token(TOKEN).build()
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
