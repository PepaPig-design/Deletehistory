import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота из переменной окружения
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Путь к файлу базы данных
DB_PATH = "history.db"

# Максимальная длина одного сообщения Telegram
MAX_MESSAGE_LENGTH = 4096

# Задержка между отправкой сообщений при восстановлении (секунды)
# Нужна чтобы не получить flood-ban от Telegram
RESTORE_DELAY = 0.1

# Задержка между удалением сообщений (секунды)
DELETE_DELAY = 0.05
