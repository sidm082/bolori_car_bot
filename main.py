from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters

TOKEN = '8178162651:AAGujfvy4MsQwcn-sI66v4Y2nyDQkVDPEvI'
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
        [KeyboardButton("📋 تمامی آگهی‌ها")]
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
    await update.message.reply_text("یک یا چند عکس برای آگهی ارسال کنید:")
    return PHOTO

async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # دریافت تمام عکس‌ها
    photos = update.message.photo
    photo_files = [photo.file_id for photo in photos]  # ذخیره کردن همه عکس‌ها

    # ذخیره عکس‌ها در داده‌های کاربر
    if 'photos' not in context.user_data:
        context.user_data['photos'] = []
    context.user_data['photos'].extend(photo_files)

    # دکمه ارسال شماره تماس
    button = KeyboardButton("📞 ارسال شماره تماس", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("لطفاً شماره تماس خود را ارسال کنید:", reply_markup=reply_markup)
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
        'photos': user_data['photos'],
        'phone': user_data['phone'],
        'username': username,
        'user_id': user.id
    }
    ads.append(ad)

    # دکمه تایید توسط ادمین
    admin_buttons = [[InlineKeyboardButton("✅ تایید آگهی", callback_data=f"approve_{len(ads)-1}")]]
    admin_markup = InlineKeyboardMarkup(admin_buttons)

    caption = f"📢 آگهی جدید برای تایید\n📝 عنوان: {ad['title']}\n📄 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}\n📞 شماره تماس: {ad['phone']}\n👤 ارسال‌کننده: {ad['username']}"
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=ad['photos'][0], caption=caption, reply_markup=admin_markup)

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
            for photo in ad['photos']:
                await context.bot.send_photo(chat_id=user_id, photo=photo, caption=caption)
        except:
            continue

    await query.edit_message_text("آگهی با موفقیت تایید و برای کاربران ارسال شد ✅")

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

    app.run_polling()

if __name__ == '__main__':
    main()
