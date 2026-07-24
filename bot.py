"""
LinkShortenerBot — sends a URL, get a short link back.

Commands:
  /start        Welcome message + instructions
  /help         Show usage instructions
  /privacy      Show the bot's privacy policy (required for Ads review)
  /stats <code> Show click count for a short link you created

Just send any http/https link as a normal message and the bot replies with
a shortened version. Clicking the short link redirects to the original URL.

This file runs TWO things at once in one process:
  1. The Telegram bot itself (polling for messages).
  2. A tiny web server that handles the actual redirects
     (yourdomain.up.railway.app/r/<code> -> original URL).
Railway gives every "web" service a free public domain, which is what makes
the short links work.
"""

import logging
import os
import re
import sqlite3
import string
import threading
from datetime import datetime, timezone
from random import choices

from flask import Flask, redirect, abort
from waitress import serve

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "links.db")
PORT = int(os.environ.get("PORT", "8080"))
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

CODE_LENGTH = 6
CODE_ALPHABET = string.ascii_letters + string.digits

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def db_connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS links (
            code TEXT PRIMARY KEY,
            original_url TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            clicks INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


def generate_unique_code(conn) -> str:
    while True:
        code = "".join(choices(CODE_ALPHABET, k=CODE_LENGTH))
        exists = conn.execute(
            "SELECT 1 FROM links WHERE code = ?", (code,)
        ).fetchone()
        if not exists:
            return code


def create_short_link(chat_id: int, original_url: str) -> str:
    conn = db_connect()
    code = generate_unique_code(conn)
    conn.execute(
        "INSERT INTO links (code, original_url, chat_id, created_at, clicks) "
        "VALUES (?, ?, ?, ?, 0)",
        (code, original_url, chat_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return code


def get_link(code: str):
    conn = db_connect()
    row = conn.execute(
        "SELECT original_url, clicks FROM links WHERE code = ?", (code,)
    ).fetchone()
    conn.close()
    return row


def increment_click(code: str):
    conn = db_connect()
    conn.execute("UPDATE links SET clicks = clicks + 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()


def get_stats_for_owner(chat_id: int, code: str):
    conn = db_connect()
    row = conn.execute(
        "SELECT original_url, clicks FROM links WHERE code = ? AND chat_id = ?",
        (code, chat_id),
    ).fetchone()
    conn.close()
    return row


WELCOME_TEXT = (
    "👋 Hi! I'm *LinkShortenerBot*.\n\n"
    "Send me any link starting with http:// or https:// and I'll give you "
    "back a short version of it.\n\n"
    "Other commands:\n"
    "/stats <code> — see how many times your short link was clicked\n"
    "/help — show this again\n"
    "/privacy — privacy policy"
)

PRIVACY_TEXT = (
    "🔒 *Privacy Policy*\n\n"
    "LinkShortenerBot stores only what is needed to operate your short "
    "links: your Telegram chat ID, the original URL you submitted, the "
    "generated short code, and a click counter. We do not collect names, "
    "phone numbers, or any other personal data, and we do not share data "
    "with third parties.\n\n"
    "Please only shorten links to legal, safe content. Links used for "
    "phishing, malware, or scams will be removed.\n\n"
    "If you'd like your data removed, message the bot owner."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown")


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PRIVACY_TEXT, parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/stats <code>`", parse_mode="Markdown")
        return
    code = context.args[0].strip()
    chat_id = update.effective_chat.id
    row = get_stats_for_owner(chat_id, code)
    if not row:
        await update.message.reply_text(
            "I couldn't find that short link under your account."
        )
        return
    original_url, clicks = row
    await update.message.reply_text(
        f"🔗 `{code}` → {original_url}\n👆 Clicks: *{clicks}*",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if not URL_RE.match(text):
        await update.message.reply_text(
            "Please send a valid link starting with http:// or https://"
        )
        return

    if not BASE_URL:
        await update.message.reply_text(
            "⚠️ The bot isn't fully configured yet (missing BASE_URL). "
            "Please try again later."
        )
        logger.warning("BASE_URL is not set — cannot create short links yet.")
        return

    chat_id = update.effective_chat.id
    code = create_short_link(chat_id, text)
    short_link = f"{BASE_URL}/r/{code}"

    await update.message.reply_text(
        f"✅ Here's your short link:\n{short_link}\n\n"
        f"Track clicks anytime with `/stats {code}`",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "LinkShortenerBot is running.", 200


@flask_app.route("/r/<code>")
def redirect_short_link(code):
    row = get_link(code)
    if not row:
        abort(404)
    original_url, _clicks = row
    increment_click(code)
    return redirect(original_url, code=302)


@flask_app.errorhandler(404)
def not_found(_e):
    return "This short link doesn't exist or has been removed.", 404


def run_web_server():
    logger.info(f"Starting redirect web server on port {PORT}...")
    serve(flask_app, host="0.0.0.0", port=PORT)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    db_connect().close()

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("privacy", privacy_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("LinkShortenerBot starting (polling mode)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
