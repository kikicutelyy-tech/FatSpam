import os
import sqlite3
import time
import asyncio
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["BOT_TOKEN"]

START_WEIGHT = 10          # 10 г
SPAM_LIMIT = 2             # первые 2 GIF/стикера бесплатно
WEIGHT_PER_SPAM = 2000     # +2 кг
SPAM_TIMEOUT = 30          # сброс через 30 секунд

DB_FILE = "weights.db"

# ID владельца секретной команды
OWNER_ID = 6277246689


# ==========================================
# RENDER WEB SERVER
# ==========================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", 10000))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Web server started on port {port}")

    server.serve_forever()


# ==========================================
# DATABASE
# ==========================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            weight INTEGER DEFAULT 10,
            PRIMARY KEY (chat_id, user_id)
        )
    """)

    conn.commit()
    conn.close()


def get_user(chat_id, user):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT weight
        FROM users
        WHERE chat_id = ? AND user_id = ?
    """, (chat_id, user.id))

    result = cursor.fetchone()

    if result is None:
        cursor.execute("""
            INSERT INTO users
            (chat_id, user_id, username, first_name, weight)
            VALUES (?, ?, ?, ?, ?)
        """, (
            chat_id,
            user.id,
            user.username,
            user.first_name,
            START_WEIGHT
        ))

        conn.commit()
        weight = START_WEIGHT

    else:
        weight = result[0]

        cursor.execute("""
            UPDATE users
            SET username = ?, first_name = ?
            WHERE chat_id = ? AND user_id = ?
        """, (
            user.username,
            user.first_name,
            chat_id,
            user.id
        ))

        conn.commit()

    conn.close()

    return weight


def add_weight(chat_id, user, amount):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET weight = weight + ?
        WHERE chat_id = ? AND user_id = ?
    """, (
        amount,
        chat_id,
        user.id
    ))

    conn.commit()

    cursor.execute("""
        SELECT weight
        FROM users
        WHERE chat_id = ? AND user_id = ?
    """, (
        chat_id,
        user.id
    ))

    weight = cursor.fetchone()[0]

    conn.close()

    return weight


# ==========================================
# FORMAT WEIGHT
# ==========================================

def format_weight(grams):
    if grams < 1000:
        return f"{grams} г"

    kg = grams // 1000
    remaining = grams % 1000

    if remaining:
        return f"{kg} кг {remaining} г"

    return f"{kg} кг"


# ==========================================
# USER MENTION
# ==========================================

def mention_user_id(user_id, name):
    return (
        f'<a href="tg://user?id={user_id}">'
        f'{escape(name)}'
        f'</a>'
    )


def mention_user(user):
    name = user.first_name or "Участник"
    return mention_user_id(user.id, name)


# ==========================================
# SPAM SYSTEM
# ==========================================

spam_counter = {}
last_spam_time = {}


async def handle_media(update, context):
    message = update.effective_message

    if not message or not message.from_user:
        return

    # Только группы
    if message.chat.type not in ("group", "supergroup"):
        return

    # GIF или стикер
    if not (message.animation or message.sticker):
        return

    user = message.from_user
    chat_id = message.chat.id

    # Счётчик отдельный для каждого пользователя
    # в каждой отдельной группе
    key = (chat_id, user.id)

    now = time.time()

    if (
        key not in last_spam_time
        or now - last_spam_time[key] > SPAM_TIMEOUT
    ):
        spam_counter[key] = 0

    spam_counter[key] = spam_counter.get(key, 0) + 1
    last_spam_time[key] = now

    # Создаём пользователя именно в этой группе
    get_user(chat_id, user)

    # Первые 2 сообщения ничего не дают
    if spam_counter[key] <= SPAM_LIMIT:
        return

    # +2 кг
    new_weight = add_weight(
        chat_id,
        user,
        WEIGHT_PER_SPAM
    )

    await message.chat.send_message(
        f"{mention_user(user)} "
        f"прибавился жир <b>+2 кг</b> за спам! 🍔\n\n"
        f"⚖️ Теперь его вес: "
        f"<b>{format_weight(new_weight)}</b>",
        parse_mode="HTML"
    )


# ==========================================
# /weight
# ==========================================

async def weight_command(update, context):
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return

    if chat.type not in ("group", "supergroup"):
        return

    weight = get_user(chat.id, user)

    await update.message.reply_text(
        f"⚖️ Вес {mention_user(user)}: "
        f"<b>{format_weight(weight)}</b>",
        parse_mode="HTML"
    )


# ==========================================
# /weights
# ==========================================

async def weights_command(update, context):
    chat = update.effective_chat

    if not chat:
        return

    if chat.type not in ("group", "supergroup"):
        return

    chat_id = chat.id

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # ТОП-20
    cursor.execute("""
        SELECT user_id, username, first_name, weight
        FROM users
        WHERE chat_id = ?
        ORDER BY weight DESC
        LIMIT 20
    """, (chat_id,))

    users = cursor.fetchall()

    # Всего участников
    cursor.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE chat_id = ?
    """, (chat_id,))

    total_users = cursor.fetchone()[0]

    # Общий вес
    cursor.execute("""
        SELECT COALESCE(SUM(weight), 0)
        FROM users
        WHERE chat_id = ?
    """, (chat_id,))

    total_weight = cursor.fetchone()[0]

    conn.close()

    # Название группы
    group_name = chat.title or "ГРУППА"

    text = (
        f"⚖️ <b>ВЕС ГРУППЫ "
        f"«{escape(group_name)}»</b>\n\n"
    )

    if users:
        medals = ["🥇", "🥈", "🥉"]

        for index, (
            user_id,
            username,
            first_name,
            weight
        ) in enumerate(users):

            name = (
                first_name
                or username
                or "Участник"
            )

            mention = mention_user_id(
                user_id,
                name
            )

            if index < 3:
                place = medals[index]
            else:
                place = f"{index + 1}."

            text += (
                f"{place} {mention} — "
                f"<b>{format_weight(weight)}</b>\n"
            )

    else:
        text += "🍔 Пока никто не набрал вес!\n"

    text += (
        f"\n👥 <b>Всего участников:</b> "
        f"{total_users}\n"
        f"🍔 <b>Всего набрано:</b> "
        f"{format_weight(total_weight)}"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ==========================================
# /fatgive — СЕКРЕТНАЯ КОМАНДА
# ==========================================

async def fatgive_command(update, context):
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    if not user or not chat or not message:
        return

    # Только владелец может использовать команду
    if user.id != OWNER_ID:
        await message.reply_text("❌ Нет доступа.")
        return

    # Только в группах
    if chat.type not in ("group", "supergroup"):
        await message.reply_text(
            "❌ Эту команду можно использовать только в группе."
        )
        return

    # Команда должна быть ответом на сообщение
    if not message.reply_to_message:
        await message.reply_text(
            "🍔 Ответь на сообщение человека и напиши:\n\n"
            "/fatgive 100000\n\n"
            "100000 = +100 кг"
        )
        return

    target = message.reply_to_message.from_user

    if not target:
        await message.reply_text("❌ Не удалось определить пользователя.")
        return

    # Проверяем количество
    if len(context.args) < 1:
        await message.reply_text(
            "❌ Укажи количество граммов.\n\n"
            "Например: /fatgive 100000"
        )
        return

    try:
        amount = int(context.args[0])
    except ValueError:
        await message.reply_text(
            "❌ Количество должно быть целым числом."
        )
        return

    # Запрещаем 0 и отрицательные значения
    if amount <= 0:
        await message.reply_text(
            "❌ Количество должно быть больше 0."
        )
        return

    # Создаём пользователя в базе, если его ещё нет
    get_user(chat.id, target)

    # Добавляем жир
    new_weight = add_weight(
        chat.id,
        target,
        amount
    )

    await message.reply_text(
        f"🍔 <b>СЕКРЕТНАЯ ОПЕРАЦИЯ</b>\n\n"
        f"{mention_user(target)} получил "
        f"<b>+{format_weight(amount)}</b>!\n\n"
        f"⚖️ Новый вес: "
        f"<b>{format_weight(new_weight)}</b>",
        parse_mode="HTML"
    )


# ==========================================
# /start
# ==========================================

async def start_command(update, context):
    await update.message.reply_text(
        "⚖️ <b>Жиро-бот запущен!</b>\n\n"
        "🍼 Начальный вес: <b>10 г</b>\n"
        "🎞️ GIF и стикеры считаются как спам\n"
        "🍔 Первые 2 сообщения — бесплатно\n"
        "⚖️ Каждый следующий GIF/стикер — <b>+2 кг</b>\n"
        "⏱️ Через 30 секунд счётчик спама сбрасывается\n\n"
        "/weight — мой вес\n"
        "/weights — вес группы",
        parse_mode="HTML"
    )


# ==========================================
# MAIN
# ==========================================

async def main():
    init_db()

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start_command)
    )

    app.add_handler(
        CommandHandler("weight", weight_command)
    )

    app.add_handler(
        CommandHandler("weights", weights_command)
    )

    # Секретная команда
    app.add_handler(
        CommandHandler("fatgive", fatgive_command)
    )

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


# ==========================================
# START
# ==========================================

if __name__ == "__main__":

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    asyncio.run(main())
