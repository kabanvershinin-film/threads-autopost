import os
import json
import asyncio
import logging
import schedule
import time
import threading
from datetime import datetime
from openai import OpenAI
from instagrapi import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ========================
# НАСТРОЙКИ — ВСТАВЬТЕ СВОИ ДАННЫЕ
# ========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "ВАШ_TELEGRAM_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", 464450106))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "ВАШ_OPENAI_КЛЮЧ")
INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "ВАШ_ЛОГИН_INSTAGRAM")
INSTAGRAM_PASSWORD = os.environ.get("INSTAGRAM_PASSWORD", "ВАШ_ПАРОЛЬ_INSTAGRAM")
POST_TIME = "09:00"  # Время публикации каждый день

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========================
# ХРАНИЛИЩЕ ПОСТОВ
# ========================
POSTS_FILE = "posts.json"

def load_posts():
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"posts": [], "current_index": 0, "active": False}

def save_posts(data):
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ========================
# ГЕНЕРАЦИЯ ПОСТОВ ЧЕРЕЗ GPT
# ========================
def generate_posts():
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = """Создай 10 постов для Threads на русском языке для аккаунта об AI технологиях.

Темы: AI видео и Seedance, нейросети и технологии, советы по контенту.

Каждый пост должен:
- Быть 3-5 предложений
- Содержать эмодзи
- Иметь 3-5 хэштегов в конце
- Быть живым и интересным
- Подходить для аудитории интересующейся AI

Формат ответа — только JSON массив:
[
  "текст поста 1",
  "текст поста 2",
  ...
]"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8
    )
    
    text = response.choices[0].message.content
    # Убираем markdown если есть
    text = text.replace("```json", "").replace("```", "").strip()
    posts = json.loads(text)
    return posts

# ========================
# ПУБЛИКАЦИЯ В THREADS
# ========================
def publish_to_threads(text):
    try:
        cl = Client()
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
        # Threads публикуется через Instagram API
        cl.direct_send(text, user_ids=[])  # placeholder
        # Реальная публикация в Threads
        result = cl.thread_publish(text)
        logger.info(f"Опубликовано в Threads: {result}")
        return True
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        return False

# ========================
# АВТОПОСТИНГ ПО РАСПИСАНИЮ
# ========================
def scheduled_post(app):
    data = load_posts()
    
    if not data["active"] or not data["posts"]:
        return
    
    idx = data["current_index"]
    if idx >= len(data["posts"]):
        data["active"] = False
        save_posts(data)
        asyncio.run(send_message(app, "✅ Все 10 постов опубликованы! Серия завершена."))
        return
    
    post_text = data["posts"][idx]
    success = publish_to_threads(post_text)
    
    if success:
        data["current_index"] += 1
        save_posts(data)
        remaining = len(data["posts"]) - data["current_index"]
        asyncio.run(send_message(app, 
            f"✅ Пост {idx + 1}/10 опубликован в Threads!\n"
            f"Осталось: {remaining} постов\n\n"
            f"📝 {post_text[:100]}..."
        ))
    else:
        asyncio.run(send_message(app, f"❌ Ошибка публикации поста {idx + 1}. Попробую завтра."))

async def send_message(app, text):
    await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

# ========================
# TELEGRAM КОМАНДЫ
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 Сгенерировать 10 постов", callback_data="generate")],
        [InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("⏹ Остановить автопостинг", callback_data="stop")],
    ]
    await update.message.reply_text(
        "🚀 *Threads Автопостинг*\n\n"
        "Управление автоматической публикацией постов в Threads.\n\n"
        "Посты публикуются каждый день в *09:00*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "generate":
        await query.edit_message_text("⏳ Генерирую 10 постов через GPT... Подождите 30 секунд")
        
        try:
            posts = generate_posts()
            posts_data = {"posts": posts, "current_index": 0, "active": False}
            save_posts(posts_data)
            
            # Показываем первые 3 поста для превью
            preview = "✅ *10 постов сгенерированы!*\n\n"
            for i, post in enumerate(posts[:3], 1):
                preview += f"*Пост {i}:*\n{post[:150]}...\n\n"
            preview += f"_...и ещё {len(posts) - 3} постов_"
            
            keyboard = [
                [InlineKeyboardButton("✅ Запустить автопостинг", callback_data="start_posting")],
                [InlineKeyboardButton("🔄 Перегенерировать", callback_data="generate")],
                [InlineKeyboardButton("👀 Показать все посты", callback_data="show_all")],
            ]
            await query.edit_message_text(
                preview,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка генерации: {e}")

    elif data == "start_posting":
        posts_data = load_posts()
        posts_data["active"] = True
        posts_data["current_index"] = 0
        save_posts(posts_data)
        await query.edit_message_text(
            "✅ *Автопостинг запущен!*\n\n"
            f"📅 Первый пост выйдет сегодня в *{POST_TIME}*\n"
            f"📊 Всего постов: 10\n"
            f"⏱ Один пост в день",
            parse_mode="Markdown"
        )

    elif data == "status":
        posts_data = load_posts()
        status = "🟢 Активен" if posts_data["active"] else "🔴 Остановлен"
        published = posts_data["current_index"]
        remaining = len(posts_data["posts"]) - published
        await query.edit_message_text(
            f"📊 *Статус автопостинга*\n\n"
            f"Статус: {status}\n"
            f"Опубликовано: {published}/10\n"
            f"Осталось: {remaining}\n"
            f"Время публикации: {POST_TIME} каждый день",
            parse_mode="Markdown"
        )

    elif data == "show_all":
        posts_data = load_posts()
        if not posts_data["posts"]:
            await query.edit_message_text("❌ Посты не сгенерированы")
            return
        text = "📝 *Все 10 постов:*\n\n"
        for i, post in enumerate(posts_data["posts"], 1):
            text += f"*{i}.* {post}\n\n"
        # Разбиваем если слишком длинно
        if len(text) > 4000:
            text = text[:4000] + "..."
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "stop":
        posts_data = load_posts()
        posts_data["active"] = False
        save_posts(posts_data)
        await query.edit_message_text("⏹ Автопостинг остановлен")

# ========================
# ЗАПУСК ПЛАНИРОВЩИКА
# ========================
def run_scheduler(app):
    schedule.every().day.at(POST_TIME).do(scheduled_post, app=app)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ========================
# ГЛАВНЫЙ ЗАПУСК
# ========================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Запускаем планировщик в отдельном потоке
    scheduler_thread = threading.Thread(target=run_scheduler, args=(app,), daemon=True)
    scheduler_thread.start()
    
    print("🤖 Threads бот запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
