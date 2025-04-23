from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
from datetime import datetime 
import os

TOKEN= os.getenv("BOT_TOKEN")  # توکن از محیط گرفته میشه
ADMIN_ID = 5677216420  # آیدی عددی ادمین را اینجا قرار بده

# مراحل ثبت آگهی
(START, TITLE, DESCRIPTION, PRICE, PHOTO, CONFIRM, PHONE) = range(7)

ads = []
users = set()
approved_ads = []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_user.id)
    keyboard = [
        [KeyboardButton("📝 ثبت آگهی")],
        [KeyboardButton("📋 تمامی آگهی‌ها")],
        [KeyboardButton("🔔 یادآوری آگهی‌های تایید نشده")]
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
    photo_file = update.message.photo[-1].file_id
    context.user_data['photo'] = photo_file

    button = KeyboardButton("📞 ارسال شماره تماس", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("لطفا شماره تماس خود را وارد کنید.توجه داشته باشید که شماره ی فقط برای ادمین قابل رویت است.", reply_markup=reply_markup)
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    context.user_data['phone'] = contact.phone_number

    buttons = [[InlineKeyboardButton("✅ ارسال برای تایید ادمین", callback_data="confirm")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("برای ارسال آگهی جهت تایید ادمین روی دکمه زیر کلیک کنید:", reply_markup=reply_markup)
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
        'date': datetime.now()  # زمان ثبت آگهی
    }
    ads.append(ad)

    # دکمه تایید توسط ادمین
    admin_buttons = [[InlineKeyboardButton("✅ تایید آگهی", callback_data=f"approve_{len(ads)-1}")]]
    admin_markup = InlineKeyboardMarkup(admin_buttons)

    caption = f"📢 آگهی جدید برای تایید\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}\n📞 شماره تماس: {ad['phone']}"
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=ad['photo'], caption=caption, reply_markup=admin_markup)

    await query.edit_message_text("آگهی شما برای بررسی به ادمین ارسال شد. ✅")
    return ConversationHandler.END

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split('_')[1])
    ad = ads[index]
    approved_ads.append(ad)

    caption = f"📢 آگهی تایید شده\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}"

    for user_id in users:
        try:
            await context.bot.send_photo(chat_id=user_id, photo=ad['photo'], caption=caption)
        except:
            continue

    await query.edit_message_text("آگهی با موفقیت تایید و برای کاربران ارسال شد ✅")

async def send_message_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        message = " ".join(context.args)
        for user_id in users:
            try:
                await context.bot.send_message(chat_id=user_id, text=message)
            except:
                continue
        await update.message.reply_text("پیام به همه کاربران ارسال شد.")

async def filter_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not approved_ads:
        await update.message.reply_text("هیچ آگهی تایید شده‌ای وجود ندارد.")
        return

    filter_type = update.message.text.lower()
    if filter_type == "کمترین قیمت":
        approved_ads.sort(key=lambda ad: float(ad['price']))
    elif filter_type == "بیشترین قیمت":
        approved_ads.sort(key=lambda ad: float(ad['price']), reverse=True)
    elif filter_type == "جدیدترین":
        approved_ads.sort(key=lambda ad: ad['date'], reverse=True)
    elif filter_type == "قدیمی‌ترین":
        approved_ads.sort(key=lambda ad: ad['date'])

    for ad in approved_ads:
        caption = f"📢 آگهی تایید شده\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}"
        try:
            await update.message.reply_photo(photo=ad['photo'], caption=caption)
        except:
            continue

    return START

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
    app.add_handler(MessageHandler(filters.TEXT, filter_ads))

    app.run_polling()
    application.run_polling()


if __name__ == '__main__':
    main()
