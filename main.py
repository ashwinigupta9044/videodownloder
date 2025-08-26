#!/usr/bin/env python3
"""
Telegram Video Downloader Bot (Python + yt-dlp)
------------------------------------------------
Fix for Python 3.13 + telegram library issues
"""

# --- Patch for Python 3.13 imghdr removal ---
try:
    import imghdr  # noqa
except ModuleNotFoundError:
    import types, sys
    imghdr = types.ModuleType("imghdr")
    def what(file, h=None):
        return None
    imghdr.what = what
    sys.modules["imghdr"] = imghdr

import asyncio
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# --- Force vendored urllib3 issue fix ---
try:
    import urllib3
except ImportError:
    os.system("pip install urllib3==2.0.7 six")
    import urllib3

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("video_bot")

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAX_UPLOAD_BYTES = 1_950_000_000  # ~1.95 GB safety
PROGRESS_EDIT_INTERVAL = 2.5  # seconds between progress message edits

# Accept most URLs; yt-dlp handles site support.
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

@dataclass
class JobState:
    last_edit_ts: float = 0.0
    progress_msg_id: Optional[int] = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Video Downloader Bot*\n\n"
        "Bas link bhejo, main download karke yahin bhej dunga.\n\n"
        "*Commands*:\n"
        "• /start – help\n"
        "• /help – usage & tips\n\n"
        "⚠️ Sirf wahi content download karein jiska aapko haq hai."
    )
    await update.message.reply_markdown(text)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*Kaise use karein*\n\n"
        "1) YouTube/TikTok/Instagram/X ka URL paste karein.\n"
        "2) Bot progress dikhayega.\n"
        "3) Video ready hote hi bhej diya jayega.\n\n"
        "*Notes*\n"
        "• Badi files (> ~1.95GB) send nahi hongi.\n"
        "• Kuch sites ke liye yt-dlp ka latest version hona zaroori hai.\n"
        "• Private/age-gated content download nahi hoga."
    )
    await update.message.reply_markdown(text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    urls = URL_RE.findall(msg.text)
    if not urls:
        await msg.reply_text("Kripya ek valid video URL bhejein.")
        return

    url = urls[0]
    await download_and_send(url, update, context)


async def download_and_send(url: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    from yt_dlp import YoutubeDL  # local import so bot can start even if yt-dlp missing

    job = JobState()
    chat_id = update.effective_chat.id

    tmpdir = Path(tempfile.mkdtemp(prefix="dl_"))
    out_tpl = str(tmpdir / "%(title).80s-%(id)s.%(ext)s")
    log.info("Downloading: %s", url)

    async def progress_hook(d):
        if d.get('status') == 'downloading':
            p = d.get('downloaded_bytes', 0)
            t = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            speed = d.get('speed') or 0
            pct = (p / t * 100) if t else 0
            text = f"⬇️ Downloading... {pct:.1f}%\n{p/1e6:.1f} / {t/1e6:.1f} MB\n{speed/1e6:.1f} MB/s"
            asyncio.get_event_loop().create_task(edit_progress(update, context, job, text))
        elif d.get('status') == 'finished':
            asyncio.get_event_loop().create_task(edit_progress(update, context, job, "✅ Download complete. Processing..."))

    ydl_opts = {
        'outtmpl': out_tpl,
        'noprogress': False,
        'progress_hooks': [progress_hook],
        'format': 'bv*+ba/b',
        'merge_output_format': 'mp4',
        'postprocessors': [
            {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}
        ],
        'quiet': True,
        'no_warnings': True,
        'retries': 3,
    }

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        sent = await update.message.reply_text("⏳ Starting download...")
        job.progress_msg_id = sent.message_id

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: _ydl_extract(ydl_opts, url))

        files = sorted(tmpdir.glob("*"))
        video_path = None
        for f in files:
            if f.suffix.lower() in {'.mp4', '.mkv', '.webm', '.mov'}:
                video_path = f
                break
        if not video_path:
            raise FileNotFoundError("Converted video not found.")

        size = video_path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            await update.message.reply_text(
                f"❌ File bahut badi hai (≈ {size/1e9:.2f} GB). 2GB se chhoti link bhejein ya quality kam karein."
            )
            return

        await edit_progress(update, context, job, "📤 Uploading to Telegram...")

        try:
            with video_path.open('rb') as fh:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=fh,
                    caption=f"✅ Done\nTitle: {info.get('title', 'Video')}\nSource: {url}",
                    supports_streaming=True,
                )
        except Exception as e:
            log.warning("send_video failed, retrying as document: %s", e)
            with video_path.open('rb') as fh:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=fh,
                    caption=f"✅ Done (as file)\nTitle: {info.get('title', 'Video')}\nSource: {url}",
                )

        await edit_progress(update, context, job, "🎉 Finished!")

    except Exception as e:
        log.exception("Download error")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def _ydl_extract(opts, url):
    from yt_dlp import YoutubeDL
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get('_type') == 'playlist' and info.get('entries'):
            info = info['entries'][0]
        return info


async def edit_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, job: JobState, text: str):
    import time
    now = time.monotonic()
    if job.progress_msg_id is None:
        m = await update.message.reply_text(text)
        job.progress_msg_id = m.message_id
        job.last_edit_ts = now
        return
    if now - job.last_edit_ts < PROGRESS_EDIT_INTERVAL:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=job.progress_msg_id,
            text=text
        )
        job.last_edit_ts = now
    except Exception as e:
        log.debug("edit_progress ignored: %s", e)


async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var not set")

    app: Application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot started")
    await app.run_polling(close_loop=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
