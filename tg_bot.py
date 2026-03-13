"""
Telegram Bot — настройка автопостинга в Threads
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

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
THREADS_USER_ID = os.getenv("THREADS_USER_ID")
THREADS_TOKEN   = os.getenv("THREADS_ACCESS_TOKEN")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://php.lingkeai.vip/api/v1")
OPENCLAW_MODEL  = os.getenv("OPENCLAW_MODEL", "gpt-5.2")
ADMIN_CHAT_ID   = int(os.getenv("ADMIN_CHAT_ID", 464450106))
PORT            = int(os.getenv("PORT", 8080))
RENDER_URL      = os.getenv("RENDER_URL", "")

THREADS_API   = "https://graph.threads.net/v1.0"
QUEUE_FILE    = "post_queue.json"
SETTINGS_FILE = "settings.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot_app = None
setup_data = {}


# ── Утилиты ───────────────────────────────────────────
def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_queue(queue):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def get_post_times(posts_per_day: int) -> list:
    """Равномерно распределяет публикации с 08:00 до 22:00"""
    start, end = 8, 22
    if posts_per_day == 1:
        return ["09:00"]
    step = (end - start) / (posts_per_day - 1)
    times = []
    for i in range(posts_per_day):
        total_minutes = start * 60 + int(step * 60 * i)
        h = total_minutes // 60
        m = total_minutes % 60
        times.append(f"{h:02d}:{m:02d}")
    return times


# ── Веб-сервер ────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_health_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()


# ── AI генерация ──────────────────────────────────────
def generate_posts_batch(topic: str, count: int) -> list:
    url = f"{OPENAI_BASE_URL}/chat/completions"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": OPENCLAW_MODEL,
            "max_tokens": 4000,
            "messages": [{
                "role": "user",
                "content": (
                    f"Напиши {count} разных коротких постов для Threads на тему: {topic}\n\n"
                    f"Требования к каждому посту:\n"
                    f"- До 300 символов\n"
                    f"- Живой разговорный стиль\n"
                    f"- 1-2 эмодзи\n"
                    f"- Без хэштегов\n"
                    f"- Только текст поста\n"
                    f"- Все посты разные по стилю и подаче\n\n"
                    f"Формат — только JSON массив из {count} строк:\n"
                    f'["пост 1", "пост 2", ...]'
                )
            }]
        },
        timeout=120
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
    container_id = r.json().get("id")
    if not container_id:
        log.error(f"Threads error: {r.json()}")
        return None
    time.sleep(5)
    r2 = requests.post(
        f"{THREADS_API}/{THREADS_USER_ID}/threads_publish",
        params={"creation_id": container_id, "access_token": THREADS_TOKEN}
    )
    return r2.json().get("id")


# ── Автопостинг ───────────────────────────────────────
def auto_post_job():
    queue = load_queue()
    if not queue:
        log.info("Очередь пуста")
        return

    post_text = queue.pop(0)
    save_queue(queue)
    post_id = publish_to_threads(post_text)

    if bot_app and ADMIN_CHAT_ID:
        import asyncio
        msg = (
            f"🤖 *Автопост опубликован!*\n\n{post_text}\n\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"📊 В очереди осталось: {len(queue)} постов"
        ) if post_id else "❌ Ошибка автопостинга"

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown"),
            loop
        )

def setup_scheduler(times: list):
    schedule.clear()
    for t in times:
        schedule.every().day.at(t).do(auto_post_job)
    log.info(f"⏰ Расписание: {times}")

def run_scheduler():
    settings = load_settings()
    if settings.get("times"):
        setup_scheduler(settings["times"])
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── /start ────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    queue = load_queue()

    if settings.get("times"):
        times_str = ", ".join(settings["times"])
        keyboard = [
            [InlineKeyboardButton("⚙️ Изменить настройки", callback_data="setup_start")],
            [InlineKeyboardButton("➕ Добавить посты в очередь", callback_data="add_more")],
            [InlineKeyboardButton("📤 Опубликовать пост сейчас", callback_data="post_now")],
            [InlineKeyboardButton("📊 Статус очереди", callback_data="queue_status")],
            [InlineKeyboardButton("🗑 Стереть все посты", callback_data="confirm_reset")],
        ]
        await update.message.reply_text(
            f"👋 Привет!\n\n"
            f"✅ *Автопостинг активен*\n"
            f"📅 Постов в день: *{settings['posts_per_day']}*\n"
            f"⏰ Время: *{times_str}*\n"
            f"📊 В очереди: *{len(queue)} постов*\n\n"
            f"Что делаем?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        keyboard = [[InlineKeyboardButton("🚀 Настроить автопостинг", callback_data="setup_start")]]
        await update.message.reply_text(
            "👋 Привет! Я публикую посты в Threads автоматически.\n\n"
            "Давай настроим автопостинг!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ── Шаги настройки ───────────────────────────────────
async def ask_posts_per_day(query):
    keyboard = [
        [InlineKeyboardButton(str(i), callback_data=f"ppd:{i}") for i in range(1, 6)],
        [InlineKeyboardButton(str(i), callback_data=f"ppd:{i}") for i in range(6, 11)],
    ]
    await query.edit_message_text(
        "⚙️ *Настройка автопостинга*\n\n"
        "📅 *Шаг 1 из 3*\n\n"
        "Сколько постов публиковать *в день*?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def ask_topics_count(query, user_id):
    ppd = setup_data[user_id]["posts_per_day"]
    times = get_post_times(ppd)
    times_str = ", ".join(times)
    keyboard = [[InlineKeyboardButton(str(i), callback_data=f"tc:{i}") for i in range(1, 4)]]
    await query.edit_message_text(
        f"✅ Постов в день: *{ppd}*\n"
        f"⏰ Время публикаций: *{times_str}*\n\n"
        f"📝 *Шаг 2 из 3*\n\n"
        f"Сколько *тем* чередовать?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def ask_next_topic(target, ctx, user_id, is_callback=True):
    data = setup_data[user_id]
    done = len(data.get("topics", []))
    total = data["topics_count"]
    text = (
        f"✅ Постов в день: *{data['posts_per_day']}*\n"
        f"✅ Тем: *{total}*\n\n"
        f"📌 *Шаг 3 из 4*\n\n"
        f"Напиши тему *{done + 1} из {total}*:\n\n"
        f"Например: _AI и нейросети_, _кино_, _технологии_"
    )
    ctx.user_data["step"] = "enter_topic"
    if is_callback:
        await target.edit_message_text(text, parse_mode="Markdown")
    else:
        await target.message.reply_text(text, parse_mode="Markdown")

async def ask_days(update, user_id):
    data = setup_data[user_id]
    keyboard = [
        [InlineKeyboardButton(str(i), callback_data=f"days:{i}") for i in range(1, 6)],
        [InlineKeyboardButton(str(i), callback_data=f"days:{i}") for i in range(6, 11)],
    ]
    await update.message.reply_text(
        f"✅ Постов в день: *{data['posts_per_day']}*\n"
        f"✅ Темы: *{', '.join(data['topics'])}*\n\n"
        f"📅 *Шаг 4 из 4*\n\n"
        f"На сколько *дней* генерировать посты?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── Обработка текста ──────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    step = ctx.user_data.get("step")

    if step != "enter_topic":
        return

    data = setup_data.setdefault(user_id, {})
    topics = data.setdefault("topics", [])
    topics.append(text)

    if len(topics) < data["topics_count"]:
        await ask_next_topic(update, ctx, user_id, is_callback=False)
        return

    # Все темы собраны — спрашиваем сколько дней
    ctx.user_data["step"] = None
    await ask_days(update, user_id)


# ── Callback ──────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "setup_start":
        setup_data[user_id] = {}
        await ask_posts_per_day(query)

    elif query.data.startswith("ppd:"):
        ppd = int(query.data.split(":")[1])
        setup_data.setdefault(user_id, {})["posts_per_day"] = ppd
        await ask_topics_count(query, user_id)

    elif query.data.startswith("tc:"):
        tc = int(query.data.split(":")[1])
        setup_data.setdefault(user_id, {})["topics_count"] = tc
        setup_data[user_id]["topics"] = []
        await ask_next_topic(query, ctx, user_id, is_callback=True)

    elif query.data == "queue_status":
        queue = load_queue()
        settings = load_settings()
        if queue:
            preview = "\n".join([f"{i+1}. {p[:70]}..." for i, p in enumerate(queue[:5])])
            await query.edit_message_text(
                f"📊 *Статус очереди*\n\n"
                f"Постов: *{len(queue)}*\n"
                f"⏰ Публикации: *{', '.join(settings.get('times', []))}*\n\n"
                f"*Ближайшие посты:*\n{preview}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("📊 *Очередь пуста*\n\nНажми /start", parse_mode="Markdown")


    elif query.data == "post_now":
        queue = load_queue()
        if not queue:
            await query.edit_message_text("📊 *Очередь пуста*\n\nНет постов для публикации.", parse_mode="Markdown")
            return
        await query.edit_message_text("📤 Публикую сейчас...")
        post_text = queue.pop(0)
        save_queue(queue)
        post_id = publish_to_threads(post_text)
        if post_id:
            await query.edit_message_text(f"✅ *Опубликовано!*\n\n{post_text}\n\n📊 В очереди осталось: {len(queue)} постов", parse_mode="Markdown")
        else:
            queue.insert(0, post_text)
            save_queue(queue)
            await query.edit_message_text("❌ Ошибка публикации. Пост возвращён в очередь.")
    elif query.data == "confirm_reset":
        queue = load_queue()
        keyboard = [
            [InlineKeyboardButton("✅ Да, стереть", callback_data="do_reset")],
            [InlineKeyboardButton("❌ Отмена", callback_data="queue_status")],
        ]
        await query.edit_message_text(
            f"🗑 *Стереть все посты?*\n\n"
            f"В очереди: *{len(queue)} постов*\n\n"
            f"Это действие нельзя отменить!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "do_reset":
        save_queue([])
        await query.edit_message_text(
            "✅ *Очередь очищена!*\n\n"
            "Все посты удалены. Нажми /start чтобы добавить новые.",
            parse_mode="Markdown"
        )

    elif query.data == "confirm_reset":
        keyboard = [
            [InlineKeyboardButton("✅ Да, стереть всё", callback_data="do_reset")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")],
        ]
        await query.edit_message_text(
            "🗑 *Вы уверены?*\n\n"
            "Это сотрёт все посты из очереди.\n"
            "Настройки расписания останутся.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "do_reset":
        save_queue([])
        queue_count = 0
        await query.edit_message_text(
            "✅ *Очередь очищена!*\n\n"
            "Нажми /start чтобы добавить новые посты.",
            parse_mode="Markdown"
        )

    elif query.data == "cancel_reset":
        await query.edit_message_text("❌ Отменено. Нажми /start")

    elif query.data == "confirm_reset":
        keyboard = [
            [InlineKeyboardButton("✅ Да, стереть всё", callback_data="do_reset")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")],
        ]
        await query.edit_message_text(
            "🗑 *Вы уверены?*\n\n"
            "Это сотрёт всю очередь постов и настройки.\n"
            "Придётся настраивать заново.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "do_reset":
        save_queue([])
        save_settings({})
        schedule.clear()
        setup_data.pop(user_id, None)
        await query.edit_message_text(
            "✅ *Всё стёрто!*\n\nНапиши /start чтобы настроить заново.",
            parse_mode="Markdown"
        )

    elif query.data == "cancel_reset":
        await query.edit_message_text("❌ Отменено. Напиши /start")

    elif query.data.startswith("days:"):
        days = int(query.data.split(":")[1])
        data = setup_data.get(user_id, {})
        data["days"] = days
        setup_data[user_id] = data

        ppd = data["posts_per_day"]
        topics = data["topics"]
        topics_total = data["topics_count"]
        total_posts = ppd * days
        posts_per_topic = max(1, total_posts // topics_total)

        await query.edit_message_text(
            f"⏳ *Генерирую {total_posts} постов на {days} дней...*\n\n"
            f"Темы: {', '.join(topics)}\n"
            f"Подожди ~{topics_total * 20} секунд ☕",
            parse_mode="Markdown"
        )

        try:
            import random
            all_posts = []
            for topic in topics:
                posts = generate_posts_batch(topic, posts_per_topic)
                all_posts.extend(posts)
            random.shuffle(all_posts)

            queue = load_queue()
            queue.extend(all_posts)
            save_queue(queue)

            times = get_post_times(ppd)
            setup_scheduler(times)
            save_settings({"posts_per_day": ppd, "topics": topics, "times": times})

            await query.message.reply_text(
                f"🎉 *Автопостинг настроен!*\n\n"
                f"📅 Постов в день: *{ppd}*\n"
                f"⏰ Время: *{', '.join(times)}*\n"
                f"📊 Постов в очереди: *{len(queue)}* ({days} дней)\n"
                f"🎯 Темы: {', '.join(topics)}\n\n"
                f"Буду публиковать сам каждый день! 🚀",
                parse_mode="Markdown"
            )
            setup_data.pop(user_id, None)

        except Exception as e:
            await query.message.reply_text(f"❌ Ошибка генерации: {e}")

    elif query.data == "add_more":
        settings = load_settings()
        setup_data[user_id] = {"posts_per_day": settings.get("posts_per_day", 1)}
        keyboard = [[InlineKeyboardButton(str(i), callback_data=f"tc:{i}") for i in range(1, 4)]]
        await query.edit_message_text(
            "➕ *Добавить посты в очередь*\n\nСколько тем использовать?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


def keep_alive():
    """Пингует сам себя каждые 10 минут чтобы Render не засыпал"""
    while True:
        time.sleep(600)
        if RENDER_URL:
            try:
                requests.get(RENDER_URL, timeout=10)
                log.info("keep-alive ping OK")
            except Exception as e:
                log.warning(f"keep-alive error: {e}")

# ── Запуск ────────────────────────────────────────────
def main():
    global bot_app
    log.info("🤖 Бот запущен!")
    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_app = app
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
