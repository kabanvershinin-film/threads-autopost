"""
Telegram Bot — автопостинг в Threads + поиск клиентов
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
REPLIED_FILE  = "replied_posts.json"
HUNTER_FILE   = "hunter_settings.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot_app = None
setup_data = {}

KEYWORDS = [
    "где сделать ai видео", "как создать видео нейросеть", "где заказать ai видео",
    "хочу видео нейросеть", "как сделать видео из фото", "нужно видео с нейросетью",
    "хочу заказать видео", "где делают ai видео", "кто делает видео нейросеть",
    "дорого делать видео", "хочу сделать видео но дорого", "бюджетное видео ai",
    "дешевое ai видео", "где дешево сделать видео", "недорогое видео нейросеть",
    "сколько стоит ai видео", "где заказать видео недорого",
    "ищу видео генератор", "посоветуйте видео ai", "какой сервис для видео",
    "какой ai для видео", "лучший сервис ai видео", "аналог sora", "аналог runway",
    "runway дорого", "sora альтернатива",
    "нейросеть видео", "ai видео генератор", "видео из текста ai",
    "текст в видео нейросеть", "генерация видео ai", "создать видео промпт",
    "сделать рекламное видео ai", "видео для рилс нейросеть", "ai видео для соцсетей",
    "короткое видео нейросеть", "видео для инстаграм ai", "сделать клип нейросеть",
    "я новичок в ai", "я новичок в нейросетях", "только начинаю с ai",
    "не разбираюсь в нейросетях", "не понимаю как работает ai", "хочу научиться ai",
    "с чего начать в ai", "с чего начать нейросети", "помогите разобраться с ai",
    "первый раз с нейросетью", "первый раз пробую ai", "что такое нейросети",
    "хочу изучить ai", "посоветуйте для новичка",
    "где найти сообщество по ai", "есть ли клуб по нейросетям", "хочу в ai сообщество",
    "где общаться про ai", "ищу единомышленников ai", "сообщество по видео ai",
    "ai комьюнити на русском", "ищу ai клуб", "где учиться ai вместе",
]


# Утилиты
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

def load_replied():
    if os.path.exists(REPLIED_FILE):
        with open(REPLIED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_replied(replied: set):
    with open(REPLIED_FILE, "w") as f:
        json.dump(list(replied), f)

def load_hunter_settings():
    if os.path.exists(HUNTER_FILE):
        with open(HUNTER_FILE, "r") as f:
            return json.load(f)
    return {"active": False, "interval": 30}

def save_hunter_settings(s):
    with open(HUNTER_FILE, "w") as f:
        json.dump(s, f)

def get_post_times(posts_per_day: int) -> list:
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


# Веб-сервер
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_health_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()

def keep_alive():
    while True:
        time.sleep(600)
        if RENDER_URL:
            try:
                requests.get(RENDER_URL, timeout=10)
                log.info("keep-alive ping OK")
            except Exception as e:
                log.warning(f"keep-alive error: {e}")


# AI генерация
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
                    f"Формат - только JSON массив из {count} строк:\n"
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

def generate_reply(post_text: str) -> str:
    url = f"{OPENAI_BASE_URL}/chat/completions"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": OPENCLAW_MODEL,
            "max_tokens": 200,
            "messages": [{
                "role": "user",
                "content": (
                    f"Человек написал в Threads: \"{post_text}\"\n\n"
                    f"Напиши короткий живой ответ от имени обычного человека.\n\n"
                    f"Правила:\n"
                    f"- Максимум 2-3 предложения\n"
                    f"- Разговорный стиль, как другу\n"
                    f"- Без рекламы и восклицательных знаков\n"
                    f"- Без длинных тире и спецсимволов\n"
                    f"- Если человек ищет AI видео сервис: напиши что знаешь где дешевле, предложи написать в личку\n"
                    f"- Если новичок или интересуется AI: упомяни закрытый клуб ИИ-Креаторов, предложи написать в личку\n"
                    f"- Если подходит оба варианта: упомяни оба\n"
                    f"- Не используй слова: реклама, сервис, продукт, подписка\n"
                    f"- Только текст ответа без кавычек\n"
                    f"- Пиши как обычный человек, естественно и просто"
                )
            }]
        },
        timeout=30
    )
    return r.json()["choices"][0]["message"]["content"].strip()


# Threads публикация
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

def reply_to_post(post_id: str, reply_text: str) -> bool:
    try:
        r = requests.post(
            f"{THREADS_API}/{THREADS_USER_ID}/threads",
            params={
                "media_type": "TEXT",
                "text": reply_text,
                "reply_to_id": post_id,
                "access_token": THREADS_TOKEN
            },
            timeout=15
        )
        container_id = r.json().get("id")
        if not container_id:
            return False
        time.sleep(3)
        r2 = requests.post(
            f"{THREADS_API}/{THREADS_USER_ID}/threads_publish",
            params={"creation_id": container_id, "access_token": THREADS_TOKEN},
            timeout=15
        )
        return bool(r2.json().get("id"))
    except Exception as e:
        log.error(f"Reply error: {e}")
        return False


# Автопостинг
def notify_admin(msg: str):
    if bot_app and ADMIN_CHAT_ID:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown"),
            loop
        )

def auto_post_job():
    queue = load_queue()
    if not queue:
        log.info("Очередь пуста")
        return
    post_text = queue.pop(0)
    save_queue(queue)
    post_id = publish_to_threads(post_text)
    msg = (
        f"🤖 *Автопост опубликован!*\n\n{post_text}\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"📊 В очереди осталось: {len(queue)} постов"
    ) if post_id else "❌ Ошибка автопостинга"
    notify_admin(msg)

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


# Поиск клиентов
def search_threads_posts(keyword: str) -> list:
    try:
        r = requests.get(
            f"{THREADS_API}/threads",
            params={"q": keyword, "fields": "id,text,username", "access_token": THREADS_TOKEN},
            timeout=15
        )
        return r.json().get("data", [])
    except Exception as e:
        log.error(f"Search error: {e}")
        return []

def hunter_job():
    hunter = load_hunter_settings()
    if not hunter.get("active"):
        return

    replied = load_replied()
    found_count = 0

    while True:
        log.info("🔍 Hunter: начинаю поиск...")
        
        for keyword in KEYWORDS:
            posts = search_threads_posts(keyword)
            for post in posts:
                post_id = post.get("id")
                post_text = post.get("text", "")
                if not post_id or post_id in replied:
                    continue
                try:
                    reply = generate_reply(post_text)
                    success = reply_to_post(post_id, reply)
                    if success:
                        replied.add(post_id)
                        found_count += 1
                        log.info(f"✅ Ответил: {post_text[:50]}...")
                        notify_admin(
                            f"🎯 *Нашёл клиента!*\n\n"
                            f"*Пост:* {post_text[:150]}\n\n"
                            f"*Ответ:* {reply}\n\n"
                            f"🔑 Ключ: _{keyword}_"
                        )
                        save_replied(replied)
                        time.sleep(10)
                        break
                except Exception as e:
                    log.error(f"Hunter error: {e}")
            else:
                continue
            break

        log.info(f"🔍 Hunter: ответил на {found_count} постов, ждем 10 минут...")
        time.sleep(600)

def run_hunter_scheduler():
    while True:
        hunter = load_hunter_settings()
        interval = hunter.get("interval", 30)
        schedule.every(interval).minutes.do(hunter_job)
        time.sleep(interval * 60)
        schedule.run_pending()


# Telegram /start
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    queue = load_queue()
    hunter = load_hunter_settings()
    h = "✅" if hunter.get("active") else "❌"

    if settings.get("times"):
        times_str = ", ".join(settings["times"])
        keyboard = [
            [InlineKeyboardButton("⚙️ Изменить настройки", callback_data="setup_start")],
            [InlineKeyboardButton("➕ Добавить посты в очередь", callback_data="add_more")],
            [InlineKeyboardButton("📤 Опубликовать пост сейчас", callback_data="post_now")],
            [InlineKeyboardButton("📊 Статус очереди", callback_data="queue_status")],
            [InlineKeyboardButton(f"🎯 Поиск клиентов {h}", callback_data="hunter_menu")],
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
            "👋 Привет! Я публикую посты в Threads автоматически.\n\nДавай настроим!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def cmd_start_from_callback(query):
    settings = load_settings()
    queue = load_queue()
    hunter = load_hunter_settings()
    h = "✅" if hunter.get("active") else "❌"

    if settings.get("times"):
        times_str = ", ".join(settings["times"])
        keyboard = [
            [InlineKeyboardButton("⚙️ Изменить настройки", callback_data="setup_start")],
            [InlineKeyboardButton("➕ Добавить посты в очередь", callback_data="add_more")],
            [InlineKeyboardButton("📤 Опубликовать пост сейчас", callback_data="post_now")],
            [InlineKeyboardButton("📊 Статус очереди", callback_data="queue_status")],
            [InlineKeyboardButton(f"🎯 Поиск клиентов {h}", callback_data="hunter_menu")],
            [InlineKeyboardButton("🗑 Стереть все посты", callback_data="confirm_reset")],
        ]
        await query.edit_message_text(
            f"👋 Главное меню\n\n"
            f"✅ *Автопостинг активен*\n"
            f"📅 Постов в день: *{settings['posts_per_day']}*\n"
            f"⏰ Время: *{times_str}*\n"
            f"📊 В очереди: *{len(queue)} постов*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# Шаги настройки
async def ask_posts_per_day(query):
    keyboard = [
        [InlineKeyboardButton(str(i), callback_data=f"ppd:{i}") for i in range(1, 6)],
        [InlineKeyboardButton(str(i), callback_data=f"ppd:{i}") for i in range(6, 11)],
    ]
    await query.edit_message_text(
        "⚙️ *Настройка автопостинга*\n\n📅 *Шаг 1 из 4*\n\nСк
