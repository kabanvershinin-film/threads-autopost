"""
Telegram Bot — пишешь тему → генерирует пост → публикует в Threads
"""

import os
import time
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
THREADS_USER_ID   = os.getenv("THREADS_USER_ID")
THREADS_TOKEN     = os.getenv("THREADS_ACCESS_TOKEN")
OPENAI_BASE_URL   = os.getenv("OPENAI_BASE_URL", "https://php.lingkeai.vip/api/v1")
OPENCLAW_MODEL    = os.getenv("OPENCLAW_MODEL", "gpt-5.2")
PORT              = int(os.getenv("PORT", 8080))

THREADS_API = "https://graph.threads.net/v1.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

drafts = {}


# ── Веб-сервер для Render ─────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# ── AI генерация ─────────────────────────────────────────────────
def generate_post(topic: str) -> str:
    url = f"{OPENAI_BASE_URL}/chat/completions"
    log.info(f"Запрос к API: {url}")
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": OPENCLAW_MODEL,
            "max_tokens": 400,
            "messages": [{
                "role": "user",
                "content": (
                    f"Напиши короткий интересный пост для Threads на тему: {topic}\n\n"
                    f"Требования:\n"
                    f"- До 300 символов\n"
                    f"- Живой разговорный стиль\n"
                    f"- 1-2 эмодзи\n"
                    f"- Без хэштегов\n"
                    f"- Только текст поста, без пояснений"
                )
            }]
        },
        timeout=30
    )
    log.info(f"API status: {r.status_code}, response: {r.text[:300]}")
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


# ── Threads публикация ───────────────────────────────────────────
def publish_to_threads(text: str) -> str | None:
    r = requests.post(
        f"{THREADS_API}/{THREADS_USER_ID}/threads",
        params={"media_type": "TEXT", "text": text, "access_token": THREADS_TOKEN}
    )
    log.info(f"Threads create: {r.json()}")
    container_id = r.json().get("id")
    if not container_id:
        log.error(f"Threads error: {r.json()}")
        return None
    time.sleep(5)
    r2 = requests.post(
        f"{THREADS_API}/{THREADS_USER_ID}/threads_publish",
        params={"creation_id": container_id, "access_token": THREADS_TOKEN}
    )
    log.info(f"Threads publish: {r2.json()}")
    return r2.json().get("id")


# ── Telegram handlers ────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я публикую посты в Threads.\n\n"
        "Просто напиши тему — сгенерирую пост!\n\n"
        "Например: *новости AI*",
        parse_mode="Markdown"
    )

async def handle_topic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    topic = update.message.text.strip()
    await update.message.reply_text("⏳ Генерирую пост...")
    try:
        post_text = generate_post(topic)
        drafts[user_id] = post_text
        keyboard = [[
            InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
            InlineKeyboardButton("🔄 Заново", callback_data=f"regen:{topic}"),
        ], [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]]
        await update.message.reply_text(
            f"📝 *Вот твой пост:*\n\n{post_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        log.error(f"Generate error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "publish":
        post_text = drafts.get(user_id)
        if not post_text:
            await query.edit_message_text("❌ Пост не найден. Напиши тему заново.")
            return
        await query.edit_message_text("📤 Публикую в Threads...")
        post_id = publish_to_threads(post_text)
        if post_id:
            await query.edit_message_text(
                f"✅ *Пост опубликован!*\n\n{post_text}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Ошибка публикации.")

    elif query.data.startswith("regen:"):
        topic = query.data.split(":", 1)[1]
        await query.edit_message_text("⏳ Генерирую новый вариант...")
        try:
            post_text = generate_post(topic)
            drafts[user_id] = post_text
            keyboard = [[
                InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
                InlineKeyboardButton("🔄 Заново", callback_data=f"regen:{topic}"),
            ], [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]]
            await query.edit_message_text(
                f"📝 *Новый вариант:*\n\n{post_text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")

    elif query.data == "cancel":
        drafts.pop(user_id, None)
        await query.edit_message_text("❌ Отменено.")


# ── Запуск ───────────────────────────────────────────────────────
def main():
    log.info("🤖 Telegram бот запущен!")
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()
    log.info(f"✅ Health server запущен на порту {PORT}")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
