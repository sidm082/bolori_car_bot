from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
from datetime import datetime
import os
import sqlite3
from contextlib import closing
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set")
ADMIN_ID = 5677216420

(START, TITLE, DESCRIPTION, PRICE, PHOTO, CONFIRM, PHONE) = range(7)

users = set()
approved_ads = []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_user.id)
    keyboard = [
        [KeyboardButton("📝 ثبت آگهی")],
        [KeyboardButton("📋 تمامی آگهی‌ها")],
        [KeyboardButton("🔍 کمترین قیمت"), KeyboardButton("🔍 بیشترین قیمت")],
        [KeyboardButton("🆕 جدیدترین"), KeyboardButton("🕰 قدیمی‌ترین")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=reply_markup)
    return START
async def handle_start_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📝 ثبت آگهی":
        await update.message.reply_text("لطفاً عنوان آگهی را وارد کنید:")
        return TITLE
    elif text == "📋 تمامی آگهی‌ها":
        if not approved_ads:
            await update.message.reply_text("هنوز هیچ آگهی تایید شده‌ای وجود ندارد.")
        else:
            for ad in approved_ads:
                caption = f"📢 آگهی تایید شده\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}\n👤 ارسال‌کننده: {ad['username']}"
                try:
                    await update.message.reply_photo(photo=ad['photo'], caption=caption)
                except:
                    continue
        return START
    elif text == "🔔 یادآوری آگهی‌های تایید نشده":
        if not ads:
            await update.message.reply_text("هیچ آگهی تایید نشده‌ای وجود ندارد.")
        else:
            await update.message.reply_text("شما هنوز آگهی‌های تایید نشده دارید.")
        return START
    else:
        await update.message.reply_text("گزینه نامعتبر است. لطفاً از دکمه‌ها استفاده کنید.")
        return START

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['title'] = update.message.text
    await update.message.reply_text("توضیحات آگهی را وارد کنید:")
    return DESCRIPTION

async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['description'] = update.message.text
    await update.message.reply_text("قیمت آگهی را وارد کنید:")
    return PRICE

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['price'] = update.message.text
    await update.message.reply_text("یک عکس برای آگهی ارسال کنید:")
    return PHOTO

async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo_file = update.message.photo[-1].file_id
    except:
        await update.message.reply_text("لطفاً یک عکس معتبر ارسال کنید.")
        return PHOTO
    
    context.user_data['photo'] = photo_file
    
    button = KeyboardButton("📞 ارسال شماره تماس", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("لطفاً شماره تماس خود را وارد کنید. توجه داشته باشید که شماره شما فقط برای ادمین قابل رویت است.", reply_markup=reply_markup)
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    context.user_data['phone'] = contact.phone_number
    keyboard = [[InlineKeyboardButton("تأیید و ارسال به ادمین", callback_data="confirm")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("آگهی شما آماده است. برای تأیید نهایی کلیک کنید:", reply_markup=reply_markup)
    return CONFIRM
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
    
    # ذخیره در دیتابیس
    save_ad(ad)
    
    # ارسال به ادمین
    cursor = conn.cursor()
    cursor.execute('SELECT last_insert_rowid()')
    ad_id = cursor.fetchone()[0]
    
    admin_buttons = [[InlineKeyboardButton("✅ تایید آگهی", callback_data=f"approve_{ad_id}")]]
    admin_markup = InlineKeyboardMarkup(admin_buttons)
    
    caption = f"📢 آگهی جدید برای تایید\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}\n📞 شماره تماس: {ad['phone']}\n👤 نام کاربری: {ad['username']}"
    
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=ad['photo'],
        caption=caption,
        reply_markup=admin_markup
    )
    
    await query.edit_message_text("آگهی شما برای بررسی به ادمین ارسال شد. ✅")
    return ConversationHandler.END

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    ad_id = int(query.data.split('_')[1])
    
    with closing(sqlite3.connect('ads.db')) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM ads WHERE id = ?', (ad_id,))
        row = cursor.fetchone()
        
        if row:
            cursor.execute('UPDATE ads SET approved = 1 WHERE id = ?', (ad_id,))
            conn.commit()
            
            ad = {
                'title': row[1],
                'description': row[2],
                'price': row[3],
                'photo': row[4],
                'username': row[6],
                'date': datetime.fromisoformat(row[8])
            }
            
            # اضافه کردن آگهی به لیست approved_ads
            approved_ads.append(ad)
            
            # ارسال به کاربران
            for user_id in users:
                try:
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=ad['photo'],
                        caption=f"📢 آگهی تایید شده\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}"
                    )
                except Exception as e:
                    logger.error(f"Error sending to {user_id}: {e}")
            
            await query.edit_message_text("آگهی با موفقیت تایید و برای کاربران ارسال شد ✅")
        else:
            await query.edit_message_text("آگهی مورد نظر یافت نشد!")
async def send_message_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        message = " ".join(context.args)
        inactive_users = []
        for user_id in users:
            try:
                await context.bot.send_message(chat_id=user_id, text=message)
            except:
                inactive_users.append(user_id)
                continue
        for user_id in inactive_users:
            users.discard(user_id)
        await update.message.reply_text("پیام به همه کاربران ارسال شد.")
def init_db():
    with closing(sqlite3.connect('ads.db')) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY,
                title TEXT,
                description TEXT,
                price TEXT,
                photo TEXT,
                phone TEXT,
                username TEXT,
                user_id INTEGER,
                date TEXT,
                approved BOOLEAN DEFAULT 0
            )
        ''')
        conn.commit()

def save_ad(ad, approved=False):
    with closing(sqlite3.connect('ads.db')) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO ads (title, description, price, photo, phone, username, user_id, date, approved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            ad['title'], ad['description'], ad['price'], ad['photo'], ad['phone'],
            ad['username'], ad['user_id'], ad['date'].isoformat(), approved
        ))
        conn.commit()

def load_ads():
    conn = sqlite3.connect('ads.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM ads WHERE approved = 1')
    approved_ads = []
    for row in cursor.fetchall():
        approved_ads.append({
            'title': row[1],
            'description': row[2],
            'price': row[3],
            'photo': row[4],
            'phone': row[5],
            'username': row[6],
            'user_id': row[7],
            'date': datetime.fromisoformat(row[8])
        })
    conn.close()
    return approved_ads

# بارگذاری آگهی‌های تایید شده هنگام راه‌اندازی
approved_ads = load_ads()

async def filter_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text
    conn = sqlite3.connect('ads.db')
    cursor = conn.cursor()
    
    if command == '/lowest':
        cursor.execute('SELECT * FROM ads WHERE approved = 1 ORDER BY CAST(price AS REAL) ASC')
    elif command == '/highest':
        cursor.execute('SELECT * FROM ads WHERE approved = 1 ORDER BY CAST(price AS REAL) DESC')
    elif command == '/newest':
        cursor.execute('SELECT * FROM ads WHERE approved = 1 ORDER BY date DESC')
    elif command == '/oldest':
        cursor.execute('SELECT * FROM ads WHERE approved = 1 ORDER BY date ASC')
    else:
        await update.message.reply_text("دستور نامعتبر است.")
        conn.close()
        return
    
    ads = cursor.fetchall()
    
    if not ads:
        await update.message.reply_text("هیچ آگهی یافت نشد.")
        conn.close()
        return
    
    for ad in ads:
        caption = f"📢 آگهی تایید شده\n📝 عنوان: {ad[1]}\n📄 توضیحات: {ad[2]}\n💰 قیمت: {ad[3]}"
        try:
            await update.message.reply_photo(photo=ad[4], caption=caption)
        except Exception as e:
            print(f"Error sending ad {ad[0]}: {e}")
    
    conn.close()
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("فرآیند لغو شد.")
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            START: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_start_choice)],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            PHONE: [MessageHandler(filters.CONTACT, get_phone)],
            CONFIRM: [CallbackQueryHandler(confirm, pattern="^confirm$")]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(approve, pattern="^approve_\\d+$"))
    app.add_handler(CommandHandler('send_message', send_message_to_user, filters=filters.User(ADMIN_ID)))
    app.add_handler(CommandHandler('lowest', filter_ads))
    app.add_handler(CommandHandler('highest', filter_ads))
    app.add_handler(CommandHandler('newest', filter_ads))
    app.add_handler(CommandHandler('oldest', filter_ads))
    app.run_polling()

if __name__ == '__main__':
    init_db()
    approved_ads = load_ads()
    main()
