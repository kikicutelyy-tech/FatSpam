import os
import sqlite3
import time
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["BOT_TOKEN"]

# Сколько дают за спам
WEIGHT_PER_SPAM = 2_000  # 2 кг в граммах

# После этого времени спам-счётчик сбрасывается
SPAM_TIMEOUT = 30

# После скольких GIF/стикеров начинается наказание
SPAM_LIMIT = 2

DB_FILE = "weights.db"

spam_counter = {}
last_spam_time = {}


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            weight INTEGER DEFAULT 10
        )
    """)

    conn.commit()
    conn.close()


def get_user(user):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT weight FROM users WHERE user_id = ?",
        (user.id,)
    )

    result = cursor.fetchone()

    if result is None:
        cursor.execute(
            """
            INSERT INTO users (user_id, username, first_name, weight)
            VALUES (?, ?, ?, ?)
            """,
            (user.id, user.username, user.first_name, 10)
        )
        conn.commit()
        weight = 10
    else:
        weight = result[0]

        cursor.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
            """,
            (user.username, user.first_name, user.id)
        )
        conn.commit()

    conn.close()
    return weight


def add_weight(user, amount):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET weight = weight + ?
        WHERE user_id = ?
        """,
        (amount, user.id)
    )

    conn.commit()

    cursor.execute(
        "SELECT weight FROM users WHERE user_id = ?",
        (user.id,)
    )

    weight = cursor.fetchone()[0]

    conn.close()
    return weight


def format_weight(grams):
    if grams < 1000:
        return f"{grams} г"

    kg = grams // 1000
    remaining = grams % 1000

    if remaining:
        return f"{kg} кг {remaining} г"

    return f"{kg} кг"


def mention_user(user):
    name = user.first_name or "Участник"

    return f'<a href="tg://user?id={user.id}">{name}</a>'


async def handle_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message

    if not message or not message.from_user:
        return

    user = message.from_user

    # Только GIF и стикеры
    if not (message.animation or message.sticker):
        return

    now = time.time()

    # Проверяем таймер спама
    if (
        user.id not in last_spam_time
        or now - last_spam_time[user.id] > SPAM_TIMEOUT
    ):
        spam_counter[user.id] = 0

    spam_counter[user.id] = spam_counter.get(user.id, 0) + 1
    last_spam_time[user.id] = now

    # Создаём пользователя, если его ещё нет
    get_user(user)

    # Первые 2 сообщения не дают вес
    if spam_counter[user.id] <= SPAM_LIMIT:
        return

    # Каждое следующее сообщение = +2 кг
    new_weight = add_weight(user, WEIGHT_PER_SPAM)

    await message.chat.send_message(
        f"{mention_user(user)} прибавился жир "
        f"<b>+2 кг</b> за спам! 🍔\n\n"
        f"⚖️ Теперь его вес: <b>{format_weight(new_weight)}</b>",
        parse_mode="HTML",
    )


async def weight_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not user:
        return

    weight = get_user(user)

    await update.message.reply_text(
        f"⚖️ Вес {mention_user(user)}: "
        f"<b>{format_weight(weight)}</b>",
        parse_mode="HTML",
    )


async def weights_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT user_id, username, first_name, weight
        FROM users
        ORDER BY weight DESC
        LIMIT 20
    """)

    users = cursor.fetchall()
    conn.close()

    if not users:
        await update.message.reply_text(
            "⚖️ Пока никто не набрал вес!"
        )
        return

    text = "⚖️ <b>ТОП ПО ВЕСУ</b>\n\n"

    medals = ["🥇", "🥈", "🥉"]

    for index, (user_id, username, first_name, weight) in enumerate(users):
        name = first_name or username or "Участник"

        mention = f'<a href="tg://user?id={user_id}">{name}</a>'

        medal = medals[index] if index < 3 else f"{index + 1}."

        text += (
            f"{medal} {mention} — "
            f"<b>{format_weight(weight)}</b>\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "⚖️ <b>Жиро-бот запущен!</b>\n\n"
        "🎞️ Спам GIF/стикерами увеличивает вес.\n"
        "🍼 Начальный вес — 10 г.\n"
        "🍔 После 2 сообщений начинается набор веса.\n\n"
        "/weight — твой вес\n"
        "/weights — таблица весов",
        parse_mode="HTML"
    )


async def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("weight", weight_command))
    app.add_handler(CommandHandler("weights", weights_command))

    app.add_handler(
        MessageHandler(
            filters.ALL,
            handle_media
        )
    )

    print("Жиро-бот запущен!")

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
