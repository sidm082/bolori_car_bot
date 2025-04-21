import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, ConversationHandler, CallbackQueryHandler
import os

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# توکن ربات خود را اینجا وارد کنید
TOKEN = "7581382819:AAFqL1O8igQdRLF5f_K4YvJ1VnrehVqo5IU"

# آی‌دی عددی ادمین‌ها
ADMINS =list(map(int, os.getenv("ADMIN_IDS", "5677216420").split(",")))  # به‌جای این‌ها آی‌دی عددی ادمین‌های خودت رو بذار

# وضعیت‌ها برای ConversationHandler
TITLE, DESCRIPTION, PRICE, PHOTO, PHONE = range(5)

# دیکشنری برای نگهداری آگهی‌ها
ads = []

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("سلام! به ربات ثبت آگهی خوش اومدی. برای ثبت آگهی /newad رو بزن.")

async def new_ad(update: Update, context: CallbackContext):
    await update.message.reply_text("عنوان آگهی رو وارد کن:")
    return TITLE

async def get_title(update: Update, context: CallbackContext):
    context.user_data['title'] = update.message.text
    await update.message.reply_text("توضیحات آگهی رو وارد کن:")
    return DESCRIPTION

async def get_description(update: Update, context: CallbackContext):
    context.user_data['description'] = update.message.text
    await update.message.reply_text("قیمت آگهی رو وارد کن:")
    return PRICE

async def get_price(update: Update, context: CallbackContext):
    context.user_data['price'] = update.message.text
    await update.message.reply_text("لطفاً یک عکس برای آگهی ارسال کن:")
    return PHOTO

async def get_photo(update: Update, context: CallbackContext):
    photo = update.message.photo[-1]
    context.user_data['photo'] = photo.file_id

    # درخواست شماره تماس با دکمه اختصاصی
    contact_button = KeyboardButton("ارسال شماره تماس", request_contact=True)
    reply_markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text("لطفاً شماره تماس خودت رو ارسال کن:", reply_markup=reply_markup)
    return PHONE

async def get_phone(update: Update, context: CallbackContext):
    contact = update.message.contact
    if contact:
        phone_number = contact.phone_number
    else:
        phone_number = update.message.text

    context.user_data['phone'] = phone_number

    ad = {
        'user_id': update.effective_user.id,
        'title': context.user_data['title'],
        'description': context.user_data['description'],
        'price': context.user_data['price'],
        'photo': context.user_data['photo'],
        'phone': phone_number,
    }
    ads.append(ad)

    await update.message.reply_text("آگهی شما ثبت شد و برای بررسی به ادمین ارسال شد.", reply_markup=ReplyKeyboardRemove())

    # ارسال آگهی برای ادمین‌ها
    for admin_id in ADMINS:
        await context.bot.send_photo(
            chat_id=admin_id,
            photo=ad['photo'],
            caption=f"📢 <b>آگهی جدید:</b>\n\n📝 <b>عنوان:</b> {ad['title']}\n💬 <b>توضیحات:</b> {ad['description']}\n💰 <b>قیمت:</b> {ad['price']}\n📞 <b>شماره تماس:</b> {ad['phone']}",
            parse_mode="HTML"
        )

    return ConversationHandler.END

async def cancel(update: Update, context: CallbackContext):
    await update.message.reply_text("فرآیند ثبت آگهی لغو شد.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('newad', new_ad)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), get_phone)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
