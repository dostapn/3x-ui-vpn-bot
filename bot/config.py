"""
Загрузчик конфигурации для VPN Bot
Загружает и валидирует переменные окружения
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()


@dataclass
class Config:
    """Конфигурация бота из переменных окружения"""

    # Telegram
    bot_token: str
    admin_id: int

    # 3x-ui API
    xui_host: str
    xui_username: str
    xui_password: str

    # Server
    domain: str
    subscription_port: int

    # Database
    db_path: str

    # Logging
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        """Загрузка конфигурации из переменных окружения"""

        # Обязательные поля
        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise ValueError("BOT_TOKEN environment variable is required")

        admin_id_str = os.getenv("ADMIN_ID")
        if not admin_id_str:
            raise ValueError("ADMIN_ID environment variable is required")

        try:
            admin_id = int(admin_id_str)
        except ValueError:
            raise ValueError("ADMIN_ID must be a valid integer")

        xui_host = os.getenv("XUI_HOST", "http://localhost:2053")
        xui_username = os.getenv("XUI_USERNAME", "admin")
        xui_password = os.getenv("XUI_PASSWORD")
        if not xui_password:
            raise ValueError("XUI_PASSWORD environment variable is required")

        domain = os.getenv("DOMAIN", "localhost")
        subscription_port = int(os.getenv("SUBSCRIPTION_PORT", "2096"))

        db_path = os.getenv("DB_PATH", "/app/data/x-ui.db")
        log_level = os.getenv("LOG_LEVEL", "INFO")

        return cls(
            bot_token=bot_token,
            admin_id=admin_id,
            xui_host=xui_host,
            xui_username=xui_username,
            xui_password=xui_password,
            domain=domain,
            subscription_port=subscription_port,
            db_path=db_path,
            log_level=log_level,
        )


# Глобальный экземпляр конфигурации
config = Config.from_env()
