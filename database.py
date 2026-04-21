import aiosqlite
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """
    Инициализация базы данных.
    Создаёт таблицу messages, если она ещё не существует.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                text        TEXT NOT NULL,
                timestamp   TEXT NOT NULL
            )
        """)
        # Индекс для быстрого поиска сообщений по chat_id
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_id
            ON messages (chat_id)
        """)
        await db.commit()
        logger.info("База данных инициализирована успешно.")


async def save_message(
    chat_id: int,
    user_id: int,
    username: str | None,
    text: str
) -> None:
    """
    Сохраняет одно сообщение в базу данных.

    :param chat_id:  ID чата
    :param user_id:  ID пользователя-отправителя
    :param username: Никнейм пользователя (может быть None)
    :param text:     Текст сообщения
    """
    timestamp = datetime.utcnow().isoformat()  # Время в UTC, формат ISO 8601

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO messages (chat_id, user_id, username, text, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, user_id, username, text, timestamp)
        )
        await db.commit()
        logger.debug(
            "Сохранено: chat_id=%s, user_id=%s, username=%s",
            chat_id, user_id, username
        )


async def get_chat_history(chat_id: int) -> list[dict]:
    """
    Возвращает все сообщения указанного чата, отсортированные по времени.

    :param chat_id: ID чата
    :return: Список словарей с полями: username, user_id, text, timestamp
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем доступ к столбцам по имени
        db.row_factory = aiosqlite.Row

        async with db.execute(
            """
            SELECT username, user_id, text, timestamp
            FROM messages
            WHERE chat_id = ?
            ORDER BY timestamp ASC
            """,
            (chat_id,)
        ) as cursor:
            rows = await cursor.fetchall()

    # Конвертируем объекты Row в обычные словари
    return [dict(row) for row in rows]


async def clear_chat_history(chat_id: int) -> int:
    """
    Удаляет все сохранённые сообщения чата из базы данных.

    :param chat_id: ID чата
    :return: Количество удалённых записей
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM messages WHERE chat_id = ?",
            (chat_id,)
        )
        await db.commit()
        deleted_count = cursor.rowcount
        logger.info(
            "Удалено %d записей из БД для chat_id=%s",
            deleted_count, chat_id
        )
        return deleted_count
