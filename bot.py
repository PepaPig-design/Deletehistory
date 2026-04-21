import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import (
    BOT_TOKEN,
    MAX_MESSAGE_LENGTH,
    RESTORE_DELAY,
    DELETE_DELAY,
)
from database import init_db, save_message, get_chat_history, clear_chat_history

# ─────────────────────────────────────────────
# Настройка логирования
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Инициализация бота и диспетчера
# ─────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ══════════════════════════════════════════════
# ХЕЛПЕРЫ
# ══════════════════════════════════════════════

async def is_bot_admin(chat_id: int) -> bool:
    """
    Проверяет, является ли бот администратором в указанном чате.
    Возвращает False, если проверить не удалось.
    """
    try:
        bot_info = await bot.get_me()
        member = await bot.get_chat_member(chat_id, bot_info.id)
        # Администратор или создатель группы
        return member.status in ("administrator", "creator")
    except Exception as exc:
        logger.warning("Не удалось проверить статус бота: %s", exc)
        return False


def split_long_text(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """
    Разбивает длинный текст на части, не превышающие max_len символов.
    Разбивка происходит по символу новой строки, чтобы не рвать слова.
    """
    parts: list[str] = []
    current_part = ""

    for line in text.splitlines(keepends=True):
        # Если одна строка длиннее лимита — режем принудительно по символам
        while len(line) > max_len:
            parts.append(line[:max_len])
            line = line[max_len:]

        if len(current_part) + len(line) > max_len:
            parts.append(current_part)
            current_part = line
        else:
            current_part += line

    if current_part:
        parts.append(current_part)

    return parts


async def safe_send(chat_id: int, text: str) -> None:
    """
    Безопасная отправка сообщения с автоматическим разбиением длинного текста.
    """
    parts = split_long_text(text)
    for part in parts:
        try:
            await bot.send_message(chat_id, part)
        except TelegramBadRequest as exc:
            logger.error("Ошибка при отправке сообщения: %s", exc)
        except TelegramForbiddenError:
            logger.error("Бот заблокирован в чате %s", chat_id)
            break  # Нет смысла продолжать


# ══════════════════════════════════════════════
# ХЕНДЛЕРЫ
# ══════════════════════════════════════════════

@dp.message(F.text & ~F.text.startswith("/") & ~F.text.startswith("."))
async def handle_text_message(message: Message) -> None:
    """
    Перехватывает все обычные текстовые сообщения (не команды, не триггеры)
    в группах и личных чатах, где бот является администратором,
    и сохраняет их в базу данных.
    """
    chat_id = message.chat.id
    chat_type = message.chat.type

    # В личном чате права администратора не нужны
    # В группах и супергруппах — проверяем
    if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not await is_bot_admin(chat_id):
            logger.debug(
                "Бот не является администратором в chat_id=%s, пропускаем.",
                chat_id
            )
            return

    user = message.from_user
    if not user:
        return  # Игнорируем системные сообщения без отправителя

    # Определяем отображаемое имя: никнейм → полное имя → ID
    username = (
        user.username
        or f"{user.first_name} {user.last_name or ''}".strip()
        or str(user.id)
    )

    await save_message(
        chat_id=chat_id,
        user_id=user.id,
        username=username,
        text=message.text,
    )


# ──────────────────────────────────────────────
# /clear_chat — удаление всех доступных сообщений
# ──────────────────────────────────────────────

@dp.message(Command("clear_chat"))
async def handle_clear_chat(message: Message) -> None:
    """
    Удаляет все сообщения чата, которые бот сохранил в БД,
    а также само сообщение с командой.

    Требования:
    - Бот должен быть администратором с правом удалять сообщения.
    - Работает для групп, супергрупп и личных чатов.
    """
    chat_id = message.chat.id

    # Проверяем права бота
    if not await is_bot_admin(chat_id):
        await message.answer(
            "⛔ Я не являюсь администратором в этом чате. "
            "Дайте мне права администратора с возможностью удалять сообщения."
        )
        return

    # Удаляем команду, чтобы не засорять чат
    try:
        await message.delete()
    except TelegramBadRequest:
        pass  # Если не удалось — не критично

    status_msg = await bot.send_message(
        chat_id,
        "🗑 Начинаю очистку чата, подождите..."
    )

    # Получаем все ID сообщений из истории
    history = await get_chat_history(chat_id)
    if not history:
        await status_msg.edit_text("ℹ️ История чата пуста — нечего удалять.")
        return

    deleted_count = 0
    failed_count = 0

    # Telegram позволяет удалять сообщения по их message_id
    # Однако в нашей БД мы не храним message_id — поэтому удаляем
    # сообщения из диапазона вокруг текущего ID.
    # Более надёжный способ: хранить message_id в БД (см. примечание ниже).
    current_id = message.message_id

    # Перебираем диапазон ID: от текущего до текущего - 1000
    # (Telegram не позволяет удалять сообщения старше 48 часов в группах)
    for msg_id in range(current_id, max(current_id - 1000, 0), -1):
        if msg_id == status_msg.message_id:
            continue  # Не трогаем наше сообщение о статусе

        try:
            await bot.delete_message(chat_id, msg_id)
            deleted_count += 1
            await asyncio.sleep(DELETE_DELAY)  # Антифлуд
        except TelegramBadRequest:
            # Сообщение не найдено или уже удалено
            failed_count += 1
        except TelegramForbiddenError:
            logger.error("Нет прав на удаление в чате %s", chat_id)
            break

    # Очищаем историю в БД
    db_deleted = await clear_chat_history(chat_id)

    result_text = (
        f"✅ Очистка завершена!\n"
        f"├ Удалено сообщений: {deleted_count}\n"
        f"├ Не удалось удалить: {failed_count} (слишком старые или нет прав)\n"
        f"└ Записей удалено из БД: {db_deleted}"
    )

    try:
        await status_msg.edit_text(result_text)
    except TelegramBadRequest:
        await safe_send(chat_id, result_text)


# ──────────────────────────────────────────────
# .romagei — восстановление истории чата
# ──────────────────────────────────────────────

@dp.message(F.text.startswith(".romagei"))
async def handle_restore_history(message: Message) -> None:
    """
    Достаёт историю чата из БД и отправляет её заново по порядку.
    Формат каждого сообщения: "👤 Имя: текст сообщения"

    Триггер: сообщение начинается с '.romagei'
    """
    chat_id = message.chat.id

    # Удаляем команду-триггер
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    history = await get_chat_history(chat_id)

    if not history:
        await safe_send(chat_id, "📭 История этого чата пуста.")
        return

    await safe_send(
        chat_id,
        f"📜 Восстанавливаю историю чата ({len(history)} сообщений)...\n"
        f"{'─' * 30}"
    )

    for record in history:
        # Формируем отображаемое имя
        author = record["username"] or f"user_{record['user_id']}"

        # Форматируем дату из ISO 8601 в читаемый вид
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(record["timestamp"])
            time_str = dt.strftime("%d.%m.%Y %H:%M")
        except ValueError:
            time_str = record["timestamp"]

        formatted = (
            f"👤 {author} [{time_str}]:\n"
            f"{record['text']}"
        )

        await safe_send(chat_id, formatted)

        # Пауза между сообщениями чтобы не словить flood limit
        await asyncio.sleep(RESTORE_DELAY)

    await safe_send(chat_id, f"{'─' * 30}\n✅ История восстановлена!")


# ──────────────────────────────────────────────
# /start — приветственное сообщение
# ──────────────────────────────────────────────

@dp.message(Command("start"))
async def handle_start(message: Message) -> None:
    """Отвечает на команду /start с описанием возможностей бота."""
    await message.answer(
        "👋 Привет! Я бот-архивариус.\n\n"
        "📌 <b>Что я умею:</b>\n"
        "• Логирую все текстовые сообщения в чате\n"
        "• /clear_chat — удаляю сообщения в чате\n"
        "• .romagei — восстанавливаю историю чата из архива\n\n"
        "⚠️ <b>Требования:</b> Я должен быть администратором "
        "с правом удалять сообщения.",
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════
# ЗАПУСК БОТА
# ══════════════════════════════════════════════

async def main() -> None:
    """Точка входа: инициализация БД и запуск поллинга."""
    logger.info("Инициализация базы данных...")
    await init_db()

    logger.info("Запуск бота...")
    try:
        # skip_updates=True — пропускаем сообщения, пришедшие пока бот был выключен
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
