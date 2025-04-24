import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, ContextTypes
from dotenv import load_dotenv

# تنظیم لاگ‌گیری
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# بارگذاری متغیرهای محیطی
load_dotenv()
TOKEN = "8061166709:AAHIbdxBrEdE1aEdO3cHEUV_Y84Cqjs6npU"
if not TOKEN:
    logger.error("BOT_TOKEN is not set in environment variables")
    raise ValueError("BOT_TOKEN is not set in environment variables")

# بررسی دقیق توکن
if not TOKEN.isascii() or any(c.isspace() for c in TOKEN) or len(TOKEN) < 30:
    logger.error("BOT_TOKEN contains invalid characters, whitespace, or is too short")
    raise ValueError("BOT_TOKEN contains invalid characters, whitespace, or is too short")

ADMIN_IDS = os.getenv("ADMIN_IDS", "5677216420")
ADMIN_ID = [int(id) for id in ADMIN_IDS.split(",") if id.strip().isdigit()]
if not ADMIN_ID:
    logger.error("No valid ADMIN_IDS provided")
    raise ValueError("No valid ADMIN_IDS provided")

# تنظیمات اولیه
CHANNEL_URL = "https://t.me/bolori_car"
CHANNEL_ID = "@bolori_car"

# اتصال به دیتابیس
def get_db_connection():
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ایجاد جداول دیتابیس
conn = get_db_connection()
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
conn.close()

# مراحل ConversationHandler
AD_TITLE, AD_DESCRIPTION, AD_PRICE, AD_PHOTOS, AD_PHONE, AD_CAR_MODEL = range(1, 7)

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Membership check failed for user {user_id}: {e}")
        await update.message.reply_text("خطایی در بررسی عضویت رخ داد. لطفاً دوباره تلاش کنید.")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_membership(update, context):
        buttons = [
            [InlineKeyboardButton("ثبت آگهی (/post_ad)", callback_data="post_ad")],
            [InlineKeyboardButton("ویرایش اطلاعات (/edit_info)", callback_data="edit_info")],
            [InlineKeyboardButton("آمار کاربران(فقط ادمین) (/stats)", callback_data="stats")],
            [InlineKeyboardButton("نمایش تمامی آگهی‌ها (/show_ads)", callback_data="show_ads")]
        ]
        await update.message.reply_text(
            "به اتوگالری بلوری خوش آمدید. لطفاً یکی از دستورات زیر را انتخاب کنید:\n"
            "/post_ad - ثبت آگهی\n"
            "/edit_info - ویرایش اطلاعات\n"
            "/stats - آمار کاربران (ادمین)\n"
            "/show_ads - نمایش آگهی‌ها",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        conn = get_db_connection()
        try:
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (update.effective_user.id,))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error in start: {e}")
            await update.message.reply_text("خطایی در ثبت اطلاعات رخ داد.")
        finally:
            conn.close()
    else:
        await update.message.reply_text("⚠️ لطفا ابتدا در کانال ما عضو شوید:\n" + CHANNEL_URL)

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update, context):
        await update.message.reply_text("⚠️ لطفا ابتدا در کانال عضو شوید!")
        return ConversationHandler.END

    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        c = conn.cursor()
        user_data = c.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if not user_data or not user_data[0]:
            await update.message.reply_text("قبل از ثبت آگهی لطفاً شماره تلفن خود را وارد کنید:")
            return AD_PHONE
    except sqlite3.Error as e:
        logger.error(f"Database error in post_ad: {e}")
        await update.message.reply_text("خطایی در بررسی اطلاعات رخ داد.")
        return ConversationHandler.END
    finally:
        conn.close()

    context.user_data['ad'] = {'photos': []}
    await update.message.reply_text("لطفا عنوان آگهی را وارد کنید:")
    return AD_TITLE

async def receive_ad_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("لطفاً عنوان معتبر وارد کنید.")
        return AD_TITLE
    context.user_data['ad']['title'] = title
    await update.message.reply_text("لطفا توضیحات آگهی را وارد کنید:")
    return AD_DESCRIPTION

async def receive_ad_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("لطفاً توضیحات معتبر وارد کنید.")
        return AD_DESCRIPTION
    context.user_data['ad']['description'] = description
    await update.message.reply_text("لطفا قیمت آگهی را وارد کنید:")
    return AD_PRICE

async def receive_ad_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = update.message.text.strip()
    if not price.replace(",", "").isdigit():
        await update.message.reply_text("لطفاً قیمت را به صورت عددی وارد کنید.")
        return AD_PRICE
    context.user_data['ad']['price'] = price
    await update.message.reply_text("لطفا عکس آگهی را ارسال کنید (یا بنویسید 'هیچ' برای ادامه یا 'تمام' پس از ارسال عکس‌ها):")
    return AD_PHOTOS

async def receive_ad_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad = context.user_data['ad']
    if update.message.text and update.message.text.lower() == "هیچ":
        ad['photos'] = []
        return await save_ad(update, context)
    elif update.message.photo:
        ad['photos'].append(update.message.photo[-1].file_id)
        await update.message.reply_text("عکس دریافت شد. برای ارسال عکس دیگر، عکس بفرستید یا بنویسید 'تمام' برای اتمام.")
        return AD_PHOTOS
    elif update.message.text and update.message.text.lower() == "تمام" and ad['photos']:
        return await save_ad(update, context)
    else:
        await update.message.reply_text("لطفا یک عکس ارسال کنید یا بنویسید 'هیچ' یا 'تمام'.")
        return AD_PHOTOS

async def save_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad = context.user_data['ad']
    user_id = update.effective_user.id
    created_at = datetime.now().isoformat()
    photos = ",".join(ad['photos']) if ad['photos'] else ""
    
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('INSERT INTO ads (user_id, title, description, price, photos, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                  (user_id, ad['title'], ad['description'], ad['price'], photos, created_at))
        conn.commit()
        ad_id = c.lastrowid
        await update.message.reply_text("✅ آگهی با موفقیت ثبت شد و در انتظار تایید مدیر است.")
        
        # اطلاع‌رسانی به ادمین‌ها
        for admin_id in ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"آگهی جدید ثبت شد:\nعنوان: {ad['title']}\nID: {ad_id}\nلطفاً در پنل ادمین بررسی کنید."
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        return ConversationHandler.END
    except sqlite3.Error as e:
        logger.error(f"Database error in save_ad: {e}")
        await update.message.reply_text("خطایی در ثبت آگهی رخ داد. لطفاً دوباره تلاش کنید.")
        return ConversationHandler.END
    finally:
        conn.close()

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.replace("+", "").isdigit() or len(phone) < 10:
        await update.message.reply_text("لطفاً یک شماره تلفن معتبر وارد کنید.")
        return AD_PHONE
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('UPDATE users SET phone = ? WHERE user_id = ?', (phone, user_id))
        conn.commit()
        await update.message.reply_text("شماره تلفن ثبت شد. حالا مدل ماشین خود را وارد کنید:")
        return AD_CAR_MODEL
    except sqlite3.Error as e:
        logger.error(f"Database error in receive_phone: {e}")
        await update.message.reply_text("خطایی در ثبت اطلاعات رخ داد.")
        return AD_PHONE
    finally:
        conn.close()

async def receive_car_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("لطفاً مدل ماشین معتبر وارد کنید.")
        return AD_CAR_MODEL
    user_id = update.effective_user.id
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('UPDATE users SET car_model = ? WHERE user_id = ?', (model, user_id))
        conn.commit()
        await update.message.reply_text("✅ مدل ماشین با موفقیت ثبت شد.")
        return ConversationHandler.END
    except sqlite3.Error as e:
        logger.error(f"Database error in receive_car_model: {e}")
        await update.message.reply_text("خطایی در ثبت اطلاعات رخ داد.")
        return AD_CAR_MODEL
    finally:
        conn.close()

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.message.reply_text("❌ دسترسی ممنوع!")
        return
    conn = get_db_connection()
    try:
        c = conn.cursor()
        ads = c.execute('SELECT * FROM ads WHERE status="pending"').fetchall()
        if not ads:
            await update.message.reply_text("هیچ آگهی در انتظار تایید نیست.")
            return
        for ad in ads:
            user_info = c.execute('SELECT phone FROM users WHERE user_id = ?', (ad['user_id'],)).fetchone()
            phone = user_info['phone'] if user_info else "نامشخص"
            user = await context.bot.get_chat(ad['user_id'])
            username = user.username or f"{user.first_name} {user.last_name or ''}"
            ad_text = f"🆔 آگهی: {ad['id']}\n👤 کاربر: {username}\n📞 شماره: {phone}\n📌 عنوان: {ad['title']}"
            buttons = [[InlineKeyboardButton("تایید", callback_data=f"approve_{ad['id']}"),
                        InlineKeyboardButton("رد", callback_data=f"reject_{ad['id']}")]]
            await update.message.reply_text(ad_text, reply_markup=InlineKeyboardMarkup(buttons))
            await asyncio.sleep(0.5)  # تأخیر برای جلوگیری از محدودیت تلگرام
    except Exception as e:
        logger.error(f"Error in admin_panel: {e}")
        await update.message.reply_text("خطایی در نمایش آگهی‌ها رخ داد.")
    finally:
        conn.close()

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_ID:
        await query.message.reply_text("❌ دسترسی ممنوع!")
        return
    action, ad_id = query.data.split("_")
    conn = get_db_connection()
    try:
        c = conn.cursor()
        if action == "approve":
            c.execute('UPDATE ads SET status="approved" WHERE id=?', (ad_id,))
            await query.message.reply_text(f"آگهی {ad_id} تایید شد.")
        elif action == "reject":
            c.execute('UPDATE ads SET status="rejected" WHERE id=?', (ad_id,))
            await query.message.reply_text(f"آگهی {ad_id} رد شد.")
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error in handle_admin_action: {e}")
        await query.message.reply_text("خطایی در پردازش درخواست رخ داد.")
    finally:
        conn.close()

async def show_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    one_year_ago = datetime.now() - timedelta(days=365)
    conn = get_db_connection()
    try:
        c = conn.cursor()
        ads = c.execute("SELECT * FROM ads WHERE status='approved' AND datetime(created_at) >= ?", (one_year_ago.isoformat(),)).fetchall()
        if not ads:
            await update.message.reply_text("هیچ آگهی تایید شده‌ای وجود ندارد.")
            return
        for ad in ads:
            text = f"📌 عنوان: {ad['title']}\n💬 توضیحات: {ad['description']}\n💰 قیمت: {ad['price']}"
            try:
                if ad['photos']:
                    for photo in ad['photos'].split(","):
                        await context.bot.send_photo(chat_id=update.message.chat_id, photo=photo, caption=text)
                        await asyncio.sleep(0.5)
                else:
                    await update.message.reply_text(text)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to send ad {ad['id']}: {e}")
    except sqlite3.Error as e:
        logger.error(f"Database error in show_ads: {e}")
        await update.message.reply_text("خطایی در نمایش آگهی‌ها رخ داد.")
    finally:
        conn.close()

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        await update.message.reply_text("❌ دسترسی ممنوع!")
        return
    conn = get_db_connection()
    try:
        c = conn.cursor()
        total_users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        total_ads = c.execute('SELECT COUNT(*) FROM ads').fetchone()[0]
        await update.message.reply_text(f"📊 آمار:\nکاربران: {total_users}\nآگهی‌ها: {total_ads}")
    except sqlite3.Error as e:
        logger.error(f"Database error in stats: {e}")
        await update.message.reply_text("خطایی در نمایش آمار رخ داد.")
    finally:
        conn.close()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END

def main():
    try:
        logger.info(f"Starting bot with token: {TOKEN[:10]}...")  # لاگ امن توکن
        application = Application.builder().token(TOKEN).build()

        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("post_ad", post_ad),
                CommandHandler("edit_info", receive_phone)
            ],
            states={
                AD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_title)],
                AD_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_description)],
                AD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_price)],
                AD_PHOTOS: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), receive_ad_photos)],
                AD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
                AD_CAR_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_car_model)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            per_message=False
        )

        application.add_handler(CommandHandler("start", start))
        application.add_handler(conv_handler)
        application.add_handler(CallbackQueryHandler(handle_admin_action, pattern="^(approve|reject)_"))
        application.add_handler(CommandHandler("stats", stats))
        application.add_handler(CommandHandler("show_ads", show_ads))

        logger.info("Bot is running...")
        application.run_polling()
    except Exception as e:
        logger.error(f"Error in main: {e}")  # Fixed line
        raise

if __name__ == "__main__":
    main()
