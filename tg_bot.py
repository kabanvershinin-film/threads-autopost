"""
Telegram Bot — пишешь тему → генерирует пост → публикует в Threads
+ автопостинг по расписанию каждый день в 09:00
"""

import os
import time
import json
import logging
import threading
import requests
import schedule
from datetime import datetime
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
ADMIN_CHAT_ID     = int(os.getenv("ADMIN_CHAT_ID", 464450106))
PORT              = int(os.getenv("PORT", 8080))
AUTO_POST_TIME    = os.getenv("AUTO_POST_TIME", "09:00")

THREADS_API = "https://graph.threads.net/v1.0"
QUEUE_FILE  = "post_queue.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

drafts = {}
bot_app = None  # глобальная ссылка на приложение


# ── Очередь постов ────────────────────────────────────
def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_queue(queue):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


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


# ── AI генерация ──────────────────────────────────────
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

def generate_10_posts(topic: str) -> list:
    url = f"{OPENAI_BASE_URL}/chat/completions"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": OPENCLAW_MODEL,
            "max_tokens": 2000,
            "messages": [{
                "role": "user",
                "content": (
                    f"Напиши 10 разных коротких постов для Threads на тему: {topic}\n\n"
                    f"Требования к каждому посту:\n"
                    f"- До 300 символов\n"
                    f"- Живой разговорный стиль\n"
                    f"- 1-2 эмодзи\n"
                    f"- Без хэштегов\n"
                    f"- Только текст поста\n\n"
                    f"Формат ответа — только JSON массив из 10 строк:\n"
                    f'["пост 1", "пост 2", ..., "пост 10"]'
                )
            }]
        },
        timeout=60
    )
    data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


# ── Threads публикация ────────────────────────────────
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


# ── Автопостинг по расписанию ─────────────────────────
def auto_post_job():
    queue = load_queue()
    if not queue:
        log.info("Очередь пуста — пропускаем автопостинг")
        return

    post_text = queue.pop(0)
    save_queue(queue)

    post_id = publish_to_threads(post_text)

    if bot_app and ADMIN_CHAT_ID:
        import asyncio
        if post_id:
            msg = (
                f"🤖 *Автопост опубликован!*\n\n"
                f"{post_text}\n\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"📊 В очереди осталось: {len(queue)} постов"
            )
        else:
            msg = f"❌ Ошибка автопостинга — пост не опубликован"

        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown"),
            bot_app.loop if hasattr(bot_app, 'loop') else asyncio.get_event_loop()
        )

def run_scheduler():
    schedule.every().day.at(AUTO_POST_TIME).do(auto_post_job)
    log.info(f"⏰ Автопостинг настроен на {AUTO_POST_TIME} каждый день")
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── Telegram команды ──────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 Написать пост", callback_data="manual_post")],
        [InlineKeyboardButton("🤖 Авто: добавить 10 постов", callback_data="auto_generate")],
        [InlineKeyboardButton("📊 Статус очереди", callback_data="queue_status")],
    ]
    await update.message.reply_text(
        "👋 Привет! Я публикую посты в Threads.\n\n"
        f"⏰ Автопостинг: каждый день в *{AUTO_POST_TIME}*\n\n"
        "Выбери действие:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_topic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    topic = update.message.text.strip()
    step = ctx.user_data.get("step")

    if step == "auto_topic":
        ctx.user_data["step"] = None
        await update.message.reply_text(f"⏳ Генерирую 10 постов на тему: *{topic}*...", parse_mode="Markdown")
        try:
            posts = generate_10_posts(topic)
            queue = load_queue()
            queue.extend(posts)
            save_queue(queue)
            preview = "\n\n".join([f"*{i+1}.* {p[:100]}..." for i, p in enumerate(posts[:3])])
            await update.message.reply_text(
                f"✅ *10 постов добавлено в очередь!*\n\n"
                f"Первые 3 поста:\n\n{preview}\n\n"
                f"📊 Всего в очереди: {len(queue)} постов\n"
                f"⏰ Публикация каждый день в {AUTO_POST_TIME}",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    else:
        await update.message.reply_text("⏳ Генерирую пост...")
        try:
            post_text = generate_post(topic)
            drafts[user_id] = post_text
            keyboard = [[
                InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
                InlineKeyboardButton("🔄 Заново", callback_data=f"regen:{topic}"),
            ], [
                InlineKeyboardButton("📥 В очередь", callback_data="add_to_queue"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
            ]]
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

    if query.data == "manual_post":
        await query.edit_message_text("✍️ Напиши тему для поста:")

    elif query.data == "auto_generate":
        ctx.user_data["step"] = "auto_topic"
        await query.edit_message_text(
            "🤖 *Автогенерация 10 постов*\n\n"
            "Напиши тему — сгенерирую 10 постов и добавлю в очередь.\n"
            "Они будут публиковаться каждый день в 09:00\n\n"
            "Например: *AI видео и нейросети*",
            parse_mode="Markdown"
        )

    elif query.data == "queue_status":
        queue = load_queue()
        if queue:
            preview = "\n".join([f"{i+1}. {p[:80]}..." for i, p in enumerate(queue[:5])])
            await query.edit_message_text(
                f"📊 *Статус очереди*\n\n"
                f"Постов в очереди: *{len(queue)}*\n"
                f"⏰ Следующий пост: сегодня/завтра в {AUTO_POST_TIME}\n\n"
                f"*Ближайшие посты:*\n{preview}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "📊 *Очередь пуста*\n\n"
                "Добавьте посты через '🤖 Авто: добавить 10 постов'",
                parse_mode="Markdown"
            )

    elif query.data == "publish":
        post_text = drafts.get(user_id)
        if not post_text:
            await query.edit_message_text("❌ Пост не найден. Напиши тему заново.")
            return
        await query.edit_message_text("📤 Публикую в Threads...")
        post_id = publish_to_threads(post_text)
        if post_id:
            await query.edit_message_text(f"✅ *Пост опубликован!*\n\n{post_text}", parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ Ошибка публикации.")

    elif query.data == "add_to_queue":
        post_text = drafts.get(user_id)
        if not post_text:
            await query.edit_message_text("❌ Пост не найден.")
            return
        queue = load_queue()
        queue.append(post_text)
        save_queue(queue)
        await query.edit_message_text(
            f"📥 *Пост добавлен в очередь!*\n\n"
            f"{post_text}\n\n"
            f"📊 Всего в очереди: {len(queue)} постов",
            parse_mode="Markdown"
        )

    elif query.data.startswith("regen:"):
        topic = query.data.split(":", 1)[1]
        await query.edit_message_text("⏳ Генерирую новый вариант...")
        try:
            post_text = generate_post(topic)
            drafts[user_id] = post_text
            keyboard = [[
                InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
                InlineKeyboardButton("🔄 Заново", callback_data=f"regen:{topic}"),
            ], [
                InlineKeyboardButton("📥 В очередь", callback_data="add_to_queue"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
            ]]
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


# ── Запуск ────────────────────────────────────────────
def main():
    global bot_app
    log.info("🤖 Telegram бот запущен!")

    threading.Thread(target=run_health_server, daemon=True).start()
    log.info(f"✅ Health server запущен на порту {PORT}")

    threading.Thread(target=run_scheduler, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_app = app

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
