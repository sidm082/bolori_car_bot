import os
import sqlite3
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler
from dotenv import load_dotenv
from telegram.ext import ContextTypes

load_dotenv()

CHANNEL_URL = "https://t.me/bolori_car"
CHANNEL_ID = "@bolori_car"
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = list(map(int, os.getenv("ADMIN_IDS", "5677216420").split(",")))

conn = sqlite3.connect('bot.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY, joined INTEGER DEFAULT 0, phone TEXT, car_model TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS ads
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      title TEXT,
                      description TEXT,
                      price TEXT,
                      photos TEXT,
                      status TEXT DEFAULT 'pending',
                      FOREIGN KEY(user_id) REFERENCES users(user_id))''')
conn.commit()

AD_TITLE, AD_DESCRIPTION, AD_PRICE, AD_PHOTOS, AD_PHONE, AD_CAR_MODEL = range(1, 7)

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"Membership check failed: {e}")
        return False

def start(update: Update, context: CallbackContext):
    if check_membership(update, context):
        buttons = [
            [InlineKeyboardButton("ثبت آگهی", callback_data="post_ad")],
            [InlineKeyboardButton("ویرایش اطلاعات", callback_data="edit_info")],
            [InlineKeyboardButton("آمار کاربران(فقط ادمین)", callback_data="stats")],
            [InlineKeyboardButton("نمایش تمامی آگهی‌ها", callback_data="show_ads")]
        ]
        update.message.reply_text(
            "به اتوگالری بلوری خوش آمدید. لطفا انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (update.effective_user.id,))
        conn.commit()
    else:
        update.message.reply_text("⚠️ لطفا ابتدا در کانال ما عضو شوید:\n" + CHANNEL_URL)

def post_ad(update: Update, context: CallbackContext):
    if not check_membership(update, context):
        if update.message:
            update.message.reply_text("⚠️ لطفا ابتدا در کانال عضو شوید!")
        elif update.callback_query:
            update.callback_query.message.reply_text("⚠️ لطفا ابتدا در کانال عضو شوید!")
        return ConversationHandler.END

    context.user_data['ad'] = {}

    if update.message:
        update.message.reply_text("لطفا عنوان آگهی را وارد کنید:")
    elif update.callback_query:
        update.callback_query.message.reply_text("لطفا عنوان آگهی را وارد کنید:")

    return AD_TITLE

def receive_ad_title(update: Update, context: CallbackContext):
    context.user_data['ad']['title'] = update.message.text
    update.message.reply_text("لطفا توضیحات آگهی را وارد کنید:")
    return AD_DESCRIPTION

def receive_ad_description(update: Update, context: CallbackContext):
    context.user_data['ad']['description'] = update.message.text
    update.message.reply_text("لطفا قیمت آگهی را وارد کنید:")
    return AD_PRICE

def receive_ad_price(update: Update, context: CallbackContext):
    context.user_data['ad']['price'] = update.message.text
    update.message.reply_text("لطفا عکس آگهی را ارسال کنید (یا بنویسید 'هیچ' برای ادامه):")
    return AD_PHOTOS

def receive_ad_photos(update: Update, context: CallbackContext):
    ad = context.user_data['ad']
    if update.message.text and update.message.text.lower() == "هیچ":
        ad['photos'] = ""
    elif update.message.photo:
        ad['photos'] = update.message.photo[-1].file_id
    else:
        update.message.reply_text("لطفا یک عکس ارسال کنید یا بنویسید 'هیچ'.")
        return AD_PHOTOS
    user_id = update.effective_user.id
    try:
        c.execute('INSERT INTO ads (user_id, title, description, price, photos) VALUES (?, ?, ?, ?, ?)',
                  (user_id, ad['title'], ad['description'], ad['price'], ad['photos']))
        conn.commit()
        update.message.reply_text("✅ آگهی با موفقیت ثبت شد و در انتظار تایید مدیر است.")
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        update.message.reply_text("❌ خطایی در ثبت آگهی رخ داد. دوباره امتحان کنید.")
    return ConversationHandler.END

def edit_info(update: Update, context: CallbackContext):
    update.message.reply_text("لطفا شماره تلفن خود را وارد کنید:")
    return AD_PHONE

def receive_phone(update: Update, context: CallbackContext):
    phone = update.message.text
    user_id = update.effective_user.id
    try:
        c.execute('UPDATE users SET phone = ? WHERE user_id = ?', (phone, user_id))
        conn.commit()
        update.message.reply_text("شماره تلفن شما با موفقیت ثبت شد. حالا مدل ماشین خود را وارد کنید:")
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        update.message.reply_text("❌ خطایی رخ داد. دوباره امتحان کنید.")
        return AD_PHONE
    return AD_CAR_MODEL

def receive_car_model(update: Update, context: CallbackContext):
    car_model = update.message.text
    user_id = update.effective_user.id
    try:
        c.execute('UPDATE users SET car_model = ? WHERE user_id = ?', (car_model, user_id))
        conn.commit()
        update.message.reply_text("✅ مدل ماشین شما با موفقیت ثبت شد.")
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        update.message.reply_text("❌ خطایی رخ داد. دوباره امتحان کنید.")
        return AD_CAR_MODEL
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END

def admin_panel(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_ID:
        update.message.reply_text("❌ دسترسی ممنوع!")
        return
    ads = c.execute('SELECT * FROM ads WHERE status="pending"').fetchall()
    if not ads:
        update.message.reply_text("هیچ آگهی در انتظار تایید نیست.")
        return
    for ad in ads:
        ad_text = f"🆔 آگهی: {ad[0]}\n👤 کاربر: {ad[1]}\n📌 عنوان: {ad[2]}"
        buttons = [[InlineKeyboardButton("تایید", callback_data=f"approve_{ad[0]}"),
                    InlineKeyboardButton("رد", callback_data=f"reject_{ad[0]}")]]
        update.message.reply_text(ad_text, reply_markup=InlineKeyboardMarkup(buttons))

def show_ads(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    ads = c.execute('SELECT * FROM ads WHERE status="approved"').fetchall()
    if not ads:
        query.message.reply_text("هیچ آگهی تایید شده‌ای وجود ندارد.")
        return
    for ad in ads:
        ad_text = f"📌 عنوان: {ad[2]}\n💬 توضیحات: {ad[3]}\n💰 قیمت: {ad[4]}"
        if ad[5]:
            context.bot.send_photo(chat_id=query.message.chat.id, photo=ad[5], caption=ad_text)
        else:
            query.message.reply_text(ad_text)

def stats(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if update.effective_user.id not in ADMIN_ID:
        query.message.reply_text("❌ دسترسی ممنوع!")
        return
    total_users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    total_ads = c.execute('SELECT COUNT(*) FROM ads').fetchone()[0]
    query.message.reply_text(f"📊 آمار:\nکاربران: {total_users}\nآگهی‌ها: {total_ads}")

def handle_admin_action(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if update.effective_user.id not in ADMIN_ID:
        query.message.reply_text("❌ دسترسی ممنوع!")
        return
    action, ad_id = query.data.split("_")
    try:
        if action == "approve":
            c.execute('UPDATE ads SET status="approved" WHERE id=?', (ad_id,))
            query.message.reply_text(f"آگهی {ad_id} تایید شد.")
        elif action == "reject":
            c.execute('UPDATE ads SET status="rejected" WHERE id=?', (ad_id,))
            query.message.reply_text(f"آگهی {ad_id} رد شد.")
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        query.message.reply_text("❌ خطا در پردازش درخواست.")
def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('post', post_ad),
            CallbackQueryHandler(post_ad, pattern="^post_ad$"),
            CallbackQueryHandler(edit_info, pattern="^edit_info$")
        ],
        states={
            AD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_title)],
            AD_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_description)],
            AD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_price)],
            AD_PHOTOS: [MessageHandler(filters.PHOTO | filters.TEXT, receive_ad_photos)],
            AD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
            AD_CAR_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_car_model)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False  # اگر نیاز داری از callback query در تمام پیام‌ها استفاده بشه
    )

    # اضافه کردن هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_admin_action, pattern="^(approve|reject)_"))
    application.add_handler(CallbackQueryHandler(button_handler))

    # اجرای ربات
    application.run_polling()

if __name__ == "__main__":
    main()
