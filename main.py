import os
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ['']
user_links = {}

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Send a YouTube/video link to choose quality and download.")

# Handle video link
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    user_id = update.effective_user.id
    user_links[user_id] = url

    buttons = [
        [InlineKeyboardButton("🔊 Audio", callback_data='audio')],
        [InlineKeyboardButton("📱 360p", callback_data='360p')],
        [InlineKeyboardButton("🖥️ 720p", callback_data='720p')],
        [InlineKeyboardButton("🎞️ Best", callback_data='best')],
    ]
    reply_markup = InlineKeyboardMarkup(buttons)

    await update.message.reply_text("📥 Choose format to download:", reply_markup=reply_markup)

# Handle button press
async def format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    format_choice = query.data
    user_id = query.from_user.id
    url = user_links.get(user_id)

    await query.edit_message_text(f"⏳ Downloading {format_choice.upper()}...")

    ydl_opts = {
        'outtmpl': 'video.%(ext)s',
    }

    if format_choice == 'audio':
        ydl_opts['format'] = 'bestaudio'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif format_choice == '360p':
        ydl_opts['format'] = 'best[height<=360]'
    elif format_choice == '720p':
        ydl_opts['format'] = 'best[height<=720]'
    else:
        ydl_opts['format'] = 'best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        if format_choice == 'audio':
            filename = filename.rsplit('.', 1)[0] + ".mp3"
            await query.message.reply_audio(audio=open(filename, 'rb'))
        else:
            await query.message.reply_video(video=open(filename, 'rb'))

        os.remove(filename)

    except Exception as e:
        await query.message.reply_text(f"❌ Error: {str(e)}")

# Build application
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
app.add_handler(CallbackQueryHandler(format_choice))

app.run_polling()
