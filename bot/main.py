"""
Точка входа для VPN Telegram Bot
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import config
from bot.database import db
from bot.utils.xui_api import xui_api
from bot.handlers.client import client_router
from bot.handlers.admin import admin_router

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


async def on_startup():
    """Действия при запуске бота"""
    logger.info("=" * 50)
    logger.info("VPN Telegram Bot Starting...")
    logger.info("=" * 50)

    # Проверка подключения к БД
    try:
        user_count = db.get_user_count()
        keys_count = db.get_active_keys_count()
        logger.info(f"Database connected: {user_count} users, {keys_count} keys")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

    # Проверка подключения к 3x-ui API
    try:
        inbounds = xui_api.get_all_inbounds()
        logger.info(f"3x-ui API connected: {len(inbounds)} inbounds available")
    except Exception as e:
        logger.error(f"3x-ui API connection failed: {e}")
        raise

    logger.info(f"Admin ID: {config.admin_id}")
    logger.info(f"Domain: {config.domain}")
    logger.info("Bot is ready!")
    logger.info("=" * 50)


async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("=" * 50)
    logger.info("VPN Telegram Bot Shutting Down...")
    logger.info("=" * 50)


async def main():
    """Главная функция бота"""

    # Инициализация бота и диспетчера
    bot = Bot(token=config.bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрация роутеров (admin_router первым для обработки reply)
    dp.include_router(admin_router)
    dp.include_router(client_router)

    # Регистрация обработчиков запуска/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        # Запуск polling
        logger.info("Starting bot polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
        raise
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
