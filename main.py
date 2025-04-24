import os
import sqlite3
from datetime import datetime, timedelta
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
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(user_id) REFERENCES users(user_id))''')
conn.commit()

AD_TITLE, AD_DESCRIPTION, AD_PRICE, AD_PHOTOS, AD_PHONE, AD_CAR_MODEL = range(1, 7)

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"Membership check failed: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_membership(update, context):
        buttons = [
            [InlineKeyboardButton("ثبت آگهی", callback_data="post_ad")],
            [InlineKeyboardButton("ویرایش اطلاعات", callback_data="edit_info")],
            [InlineKeyboardButton("آمار کاربران(فقط ادمین)", callback_data="stats")],
            [InlineKeyboardButton("نمایش تمامی آگهی‌ها", callback_data="show_ads")]
        ]
        await update.message.reply_text("به اتوگالری بلوری خوش آمدید. لطفا انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons))
        c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (update.effective_user.id,))
        conn.commit()
    else:
        await update.message.reply_text("⚠️ لطفا ابتدا در کانال ما عضو شوید:\n" + CHANNEL_URL)

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update, context):
        await update.callback_query.message.reply_text("⚠️ لطفا ابتدا در کانال عضو شوید!")
        return ConversationHandler.END

    user_id = update.effective_user.id
    user_data = c.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,)).fetchone()
    if not user_data or not user_data[0]:
        await update.callback_query.message.reply_text("قبل از ثبت آگهی لطفاً شماره تلفن خود را وارد کنید:")
        return AD_PHONE

    context.user_data['ad'] = {}
    await update.callback_query.message.reply_text("لطفا عنوان آگهی را وارد کنید:")
    return AD_TITLE

async def receive_ad_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ad']['title'] = update.message.text
    await update.message.reply_text("لطفا توضیحات آگهی را وارد کنید:")
    return AD_DESCRIPTION

async def receive_ad_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ad']['description'] = update.message.text
    await update.message.reply_text("لطفا قیمت آگهی را وارد کنید:")
    return AD_PRICE

async def receive_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ad']['price'] = update.message.text
    await update.message.reply_text("لطفا عکس آگهی را ارسال کنید (یا بنویسید 'هیچ' برای ادامه):")
    return AD_PHOTOS

async def receive_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad = context.user_data['ad']
    if update.message.text and update.message.text.lower() == "هیچ":
        ad['photos'] = ""
    elif update.message.photo:
        ad['photos'] = update.message.photo[-1].file_id
    else:
        await update.message.reply_text("لطفا یک عکس ارسال کنید یا بنویسید 'هیچ'.")
        return AD_PHOTOS

    user_id = update.effective_user.id
    created_at = datetime.now().isoformat()
    c.execute('INSERT INTO ads (user_id, title, description, price, photos, created_at) VALUES (?, ?, ?, ?, ?, ?)',
              (user_id, ad['title'], ad['description'], ad['price'], ad['photos'], created_at))
    conn.commit()
    await update.message.reply_text("✅ آگهی با موفقیت ثبت شد و در انتظار تایید مدیر است.")
    return ConversationHandler.END

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text
    user_id = update.effective_user.id
    c.execute('UPDATE users SET phone = ? WHERE user_id = ?', (phone, user_id))
    conn.commit()
    await update.message.reply_text("شماره تلفن ثبت شد. حالا مدل ماشین خود را وارد کنید:")
    return AD_CAR_MODEL

async def receive_car_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text
    user_id = update.effective_user.id
    c.execute('UPDATE users SET car_model = ? WHERE user_id = ?', (model, user_id))
    conn.commit()
    await update.message.reply_text("✅ مدل ماشین با موفقیت ثبت شد.")
    return ConversationHandler.END

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.message.reply_text("❌ دسترسی ممنوع!")
        return
    ads = c.execute('SELECT * FROM ads WHERE status="pending"').fetchall()
    if not ads:
        await update.message.reply_text("هیچ آگهی در انتظار تایید نیست.")
        return
    for ad in ads:
        user_info = c.execute('SELECT phone FROM users WHERE user_id = ?', (ad[1],)).fetchone()
        phone = user_info[0] if user_info else "نامشخص"
        user = await context.bot.get_chat(ad[1])
        username = user.username or f"{user.first_name} {user.last_name or ''}"
        ad_text = f"🆔 آگهی: {ad[0]}\n👤 کاربر: {username}\n📞 شماره: {phone}\n📌 عنوان: {ad[2]}"
        buttons = [[InlineKeyboardButton("تایید", callback_data=f"approve_{ad[0]}"),
                    InlineKeyboardButton("رد", callback_data=f"reject_{ad[0]}")]]
        await update.message.reply_text(ad_text, reply_markup=InlineKeyboardMarkup(buttons))

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی ممنوع!")
        return
    action, ad_id = query.data.split("_")
    if action == "approve":
        c.execute('UPDATE ads SET status="approved" WHERE id=?', (ad_id,))
        await query.message.reply_text(f"آگهی {ad_id} تایید شد.")
    elif action == "reject":
        c.execute('UPDATE ads SET status="rejected" WHERE id=?', (ad_id,))
        await query.message.reply_text(f"آگهی {ad_id} رد شد.")
    conn.commit()

async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    one_year_ago = datetime.now() - timedelta(days=365)
    ads = c.execute("SELECT * FROM ads WHERE status='approved' AND datetime(created_at) >= ?", (one_year_ago.isoformat(),)).fetchall()
    if not ads:
        await query.message.reply_text("هیچ آگهی تایید شده‌ای وجود ندارد.")
        return
    for ad in ads:
        text = f"📌 عنوان: {ad[2]}\n💬 توضیحات: {ad[3]}\n💰 قیمت: {ad[4]}"
        if ad[5]:
            await context.bot.send_photo(chat_id=query.message.chat.id, photo=ad[5], caption=text)
        else:
            await query.message.reply_text(text)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی ممنوع!")
        return
    total_users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    total_ads = c.execute('SELECT COUNT(*) FROM ads').fetchone()[0]
    await query.message.reply_text(f"📊 آمار:\nکاربران: {total_users}\nآگهی‌ها: {total_ads}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END

def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(post_ad, pattern="^post_ad$"),
            CallbackQueryHandler(receive_phone, pattern="^edit_info$")
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
        per_message=False
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_admin_action, pattern="^(approve|reject)_"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(show_ads, pattern="^show_ads$"))

    application.run_polling()

if __name__ == "__main__":
    main()
