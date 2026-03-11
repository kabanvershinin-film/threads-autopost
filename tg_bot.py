"""
Telegram Bot — пишешь тему → Claude генерирует пост → публикует в Threads
"""

import os
import time
import logging
import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

load_dotenv()

# ─── Конфигурация ───────────────────────────────────────────────
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
THREADS_USER_ID     = os.getenv("THREADS_USER_ID")
THREADS_TOKEN       = os.getenv("THREADS_ACCESS_TOKEN")
OPENCLAW_BASE_URL   = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789")
OPENCLAW_MODEL      = os.getenv("OPENCLAW_MODEL", "gpt-5.2")

THREADS_API = "https://graph.threads.net/v1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

anthropic = Anthropic(
    api_key=ANTHROPIC_API_KEY,
    base_url=OPENCLAW_BASE_URL,
)

# ─── Хранилище черновиков ────────────────────────────────────────
drafts = {}  # user_id → текст поста


# ══════════════════════════════════════════════════════════════════
#  CLAUDE — генерация поста
# ══════════════════════════════════════════════════════════════════

def generate_post(topic: str) -> str:
    msg = anthropic.messages.create(
        model=OPENCLAW_MODEL,
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": f"""Напиши короткий интересный пост для Threads на тему: {topic}

Требования:
- До 300 символов
- Живой разговорный стиль
- 1-2 эмодзи
- Без хэштегов
- Только текст поста, без пояснений"""
        }]
    )
    return msg.content[0].text.strip()


# ══════════════════════════════════════════════════════════════════
#  THREADS API — публикация
# ══════════════════════════════════════════════════════════════════

def publish_to_threads(text: str) -> str | None:
    # Шаг 1: создать контейнер
    r = requests.post(
        f"{THREADS_API}/{THREADS_USER_ID}/threads",
        params={
            "media_type": "TEXT",
            "text": text,
            "access_token": THREADS_TOKEN,
        }
    )
    container_id = r.json().get("id")
    if not container_id:
        log.error(f"Threads container error: {r.json()}")
        return None

    time.sleep(5)

    # Шаг 2: опубликовать
    r2 = requests.post(
        f"{THREADS_API}/{THREADS_USER_ID}/threads_publish",
        params={
            "creation_id": container_id,
            "access_token": THREADS_TOKEN,
        }
    )
    post_id = r2.json().get("id")
    return post_id


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM HANDLERS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я публикую посты в Threads.\n\n"
        "Просто напиши мне тему — я сгенерирую пост и опубликую!\n\n"
        "Например: *новости AI* или *мотивация на утро*",
        parse_mode="Markdown"
    )


async def handle_topic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    topic = update.message.text.strip()

    await update.message.reply_text("⏳ Генерирую пост...")

    try:
        post_text = generate_post(topic)
        drafts[user_id] = post_text

        keyboard = [
            [
                InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
                InlineKeyboardButton("🔄 Перегенерировать", callback_data=f"regen:{topic}"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
        markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"📝 *Вот твой пост:*\n\n{post_text}",
            parse_mode="Markdown",
            reply_markup=markup
        )

    except Exception as e:
        log.error(f"Generate error: {e}")
        await update.message.reply_text("❌ Ошибка генерации. Попробуй ещё раз.")


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
                f"✅ *Пост опубликован в Threads!*\n\n{post_text}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Ошибка публикации. Проверь токен Threads.")

    elif query.data.startswith("regen:"):
        topic = query.data.split(":", 1)[1]
        await query.edit_message_text("⏳ Генерирую новый вариант...")

        try:
            post_text = generate_post(topic)
            drafts[user_id] = post_text

            keyboard = [
                [
                    InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
                    InlineKeyboardButton("🔄 Перегенерировать", callback_data=f"regen:{topic}"),
                ],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
            markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"📝 *Новый вариант:*\n\n{post_text}",
                parse_mode="Markdown",
                reply_markup=markup
            )
        except Exception as e:
            await query.edit_message_text("❌ Ошибка генерации.")

    elif query.data == "cancel":
        drafts.pop(user_id, None)
        await query.edit_message_text("❌ Отменено. Напиши новую тему когда захочешь.")


# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════

def main():
    log.info("🤖 Telegram бот запущен!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic))

    app.run_polling()


if __name__ == "__main__":
    main()
