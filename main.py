from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters

TOKEN = '8178162651:AAGujfvy4MsQwcn-sI66v4Y2nyDQkVDPEvI'
ADMIN_ID = 5677216420# آیدی عددی ادمین را اینجا قرار بده

# مراحل ثبت آگهی
(START, TITLE, DESCRIPTION, PRICE, PHOTO, CONFIRM, PHONE) = range(7)

ads = []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لطفاً عنوان آگهی را وارد کنید:")
    return TITLE

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

    # دکمه ارسال شماره تماس
    button = KeyboardButton("📞 ارسال شماره تماس", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("لطفاً شماره تماس خود را ارسال کنید:", reply_markup=reply_markup)
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    context.user_data['phone'] = contact.phone_number

    # دکمه تایید نهایی
    buttons = [[InlineKeyboardButton("✅ تایید", callback_data="confirm")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("برای تایید نهایی روی دکمه زیر کلیک کنید:", reply_markup=reply_markup)
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
        'username': username
    }
    ads.append(ad)

    caption = f"📢 آگهی جدید\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}\n📞 شماره تماس: {ad['phone']}\n👤 ارسال‌کننده: {ad['username']}"
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=ad['photo'], caption=caption)

    await query.edit_message_text("آگهی شما برای بررسی به ادمین ارسال شد. ✅")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("فرآیند لغو شد.")
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
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
    app.run_polling()

if __name__ == '__main__':
    main()
