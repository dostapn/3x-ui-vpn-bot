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


async def on_startup(bot: Bot):
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

    # Проверка обновлений и уведомление админа
    try:
        await check_bot_updates(bot)
    except Exception as e:
        logger.error(f"Failed to check updates: {e}")

    # Проверка подключения к 3x-ui API
    try:
        inbounds = xui_api.get_all_inbounds()
        logger.info(f"3x-ui API connected: {len(inbounds)} inbounds available")
    except Exception as e:
        logger.error(f"3x-ui API connection failed: {e}")
        raise

    logger.info(f"Admin ID: {config.admin_id}")
    logger.info(f"Domain: {config.domain}")
    logger.info(f"Version: {config.version}")
    logger.info("Bot is ready!")
    logger.info("=" * 50)


async def check_bot_updates(bot: Bot):
    """Проверка версии и отправка уведомлений об обновлениях админу"""
    # Получаем последнюю и текущую версию из БД
    last_version = db.get_setting("current_version", "1.0.0")
    current_version = config.version

    # Если текущая версия совпадает с последней, выходим
    if current_version == last_version:
        return

    logger.info(f"New version detected: {current_version} (last: {last_version})")

    # Парсим CHANGELOG.md
    updates = parse_changelog(last_version)
    logger.info(f"Found {len(updates)} update blocks to notify")

    # Если нет обновлений, обновляем версию в БД и выходим
    if not updates:
        db.update_setting("current_version", current_version)
        return

    # Отправляем уведомления админу (от старых к новым)
    from aiogram.utils.markdown import html_decoration as hd

    for version, date, text in updates:
        logger.info(f"Sending update notification for v{version}")

        # Экранируем спецсимволы HTML внутри текста
        safe_text = hd.quote(text)

        # Форматируем как код-блок с типом markdown для подсветки синтаксиса
        message = (
            f"🚀 <b>Обновление v{version} ({date})</b>\n\n"
            f"<pre><code class='language-markdown'>{safe_text}</code></pre>"
        )
        try:
            await bot.send_message(chat_id=config.admin_id, text=message, parse_mode="HTML")
            # Обновляем версию в БД сразу после успешной отправки
            db.update_setting("current_version", version)
            await asyncio.sleep(1.5)  # Пауза между сообщениями
        except Exception as e:
            logger.error(f"Failed to send update notification for v{version}: {e}")

    # Финальное обновление до текущей версии из конфига
    db.update_setting("current_version", current_version)


def parse_changelog(last_version: str) -> list:
    """Парсинг CHANGELOG.md для получения изменений новее last_version"""
    import re
    import os

    changelog_path = os.path.join(os.path.dirname(__file__), "..", "CHANGELOG.md")
    if not os.path.exists(changelog_path):
        logger.warning("CHANGELOG.md not found")
        return []

    with open(changelog_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Регулярка для поиска версий: ## [1.2.1] - 2026-01-22
    version_blocks = re.split(r"\n(?=## \[\d+\.\d+\.\d+\])", content)

    updates = []
    for block in version_blocks:
        match = re.search(r"## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})", block)
        if not match:
            continue

        version = match.group(1)
        date = match.group(2)

        # Сравниваем версии (простая логика для x.y.z)
        if is_version_newer(version, last_version):
            text = block.split("\n", 1)[1].strip()
            text = re.sub(r"\n---\n", "\n", text)
            updates.append((version, date, text))

    # Сортируем от старых к новым
    updates.sort(key=lambda x: [int(p) for p in x[0].split(".")])
    return updates


def is_version_newer(v1: str, v2: str) -> bool:
    """Сравнение версий x.y.z"""
    p1 = [int(p) for p in v1.split(".")]
    p2 = [int(p) for p in v2.split(".")]
    return p1 > p2


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
    dp.startup.register(lambda: on_startup(bot))
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
