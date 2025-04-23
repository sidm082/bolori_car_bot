from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
import json
import os
import sqlite3

# اتصال به دیتابیس SQLite (اگر دیتابیس موجود نباشد، ساخته می‌شود)
conn = sqlite3.connect('ads_database.db')

# ایجاد یک کرسر برای اجرا کردن دستورات SQL
cursor = conn.cursor()

# ایجاد جدول جدید (اگر وجود نداشته باشد)
cursor.execute('''
CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    ad_info TEXT,
    status TEXT
)
''')

# ذخیره تغییرات و بستن اتصال
conn.commit()
conn.close()

# فایل آگهی‌ها
ADS_FILE = 'ads.json'
APPROVED_ADS_FILE = 'approved_ads.json'

# لیست آگهی‌های تایید شده
approved_ads = []

# تنظیمات اولیه
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set")
ADMIN_ID = 5677216420  # جایگزین با آی دی ادمین واقعی
# تعریف مراحل ConversationHandler
(START, TITLE, DESCRIPTION, PRICE, PHOTO, CONFIRM, PHONE) = range(7)

# متغیرهای جهانی
users = set()
approved_ads = []

# بارگذاری آگهی‌ها از فایل
def load_ads():
    global approved_ads
    if os.path.exists(APPROVED_ADS_FILE):
        with open(APPROVED_ADS_FILE, 'r', encoding='utf-8') as f:
            approved_ads = json.load(f)

# ذخیره آگهی‌ها در فایل
def save_ads():
    with open(APPROVED_ADS_FILE, 'w', encoding='utf-8') as f:
        json.dump(approved_ads, f, ensure_ascii=False, indent=4)

# ذخیره آگهی جدید
def save_ad(ad):
    ads = load_ads_from_file()
    ads.append(ad)
    with open(ADS_FILE, 'w', encoding='utf-8') as f:
        json.dump(ads, f, ensure_ascii=False, indent=4)

# بارگذاری آگهی‌ها از فایل
def load_ads_from_file():
    if os.path.exists(ADS_FILE):
        with open(ADS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

# ارسال پیام به کاربران
def send_message_to_users(update, context, message):
    with open('users.json', 'r', encoding='utf-8') as f:
        users = json.load(f)
    
    for user_id in users:
        try:
            context.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            print(f"Error sending message to {user_id}: {e}")

# دستور /start
def start(update, context):
    update.message.reply_text("سلام! به ربات آگهی‌ها خوش آمدید.")

# دستور ارسال آگهی
def submit_ad(update, context):
    ad_info = ' '.join(context.args)
    if ad_info:
        # آگهی جدید ثبت می‌شود
        ad = {
            "user_id": update.message.from_user.id,
            "ad_info": ad_info,
            "status": "pending"
        }
        save_ad(ad)
        update.message.reply_text("آگهی شما با موفقیت ثبت شد و منتظر تایید است.")
    else:
        update.message.reply_text("لطفاً اطلاعات آگهی خود را وارد کنید.")

# تایید آگهی توسط ادمین
def approve_ad(update, context):
    if update.message.from_user.id == ADMIN_USER_ID:
        ad_id = int(context.args[0]) if context.args else None
        if ad_id is not None:
            ads = load_ads_from_file()
            if 0 <= ad_id < len(ads) and ads[ad_id]["status"] == "pending":
                ads[ad_id]["status"] = "approved"
                approved_ads.append(ads[ad_id])
                save_ads()
                save_ads_to_file(ads)
                update.message.reply_text(f"آگهی با شماره {ad_id} تایید شد.")
            else:
                update.message.reply_text("آگهی موجود نیست یا قبلاً تایید شده است.")
        else:
            update.message.reply_text("لطفاً شماره آگهی را وارد کنید.")
    else:
        update.message.reply_text("شما مجوز تایید آگهی ندارید.")

# دستور نمایش آگهی‌های تایید شده
def show_approved_ads(update, context):
    if len(approved_ads) == 0:
        update.message.reply_text("هیچ آگهی تایید شده‌ای وجود ندارد.")
    else:
        for ad in approved_ads:
            update.message.reply_text(f"آگهی: {ad['ad_info']}")

# تعریف توابع و راه‌اندازی ربات
def main():
    load_ads()  # بارگذاری آگهی‌ها
    updater = Updater('YOUR_TOKEN', use_context=True)
    
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("submit_ad", submit_ad))
    dp.add_handler(CommandHandler("approve_ad", approve_ad))
    dp.add_handler(CommandHandler("show_approved_ads", show_approved_ads))
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
