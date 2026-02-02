import logging
from datetime import datetime, timedelta
from typing import Sequence, Mapping

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from bot.config import config
from bot.database import db

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def format_bytes(size: int) -> str:
    """Форматирует количество байтов и возвращает понятную строку."""
    power = 2**10
    magnitude = 0
    prefixes = ("", "K", "M", "G", "T")
    while size >= power and magnitude + 1 < len(prefixes):
        size /= power
        magnitude += 1
    return f"{size:.2f} {prefixes[magnitude]}B"


async def send_report(bot: Bot, title: str, stats: Sequence[Mapping[str, int | str]]):
    """Формирует и отправляет админу текстовый отчёт по трафику."""
    if not stats:
        return

    header = (
        f"📊 <b>{title}</b>\n\n" f"<pre>" f"{'Профиль':<15} {'↑':<9} {'↓':<9}\n" f"{'-' * 35}\n"
    )
    footer = f"</pre>\n📅 <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"

    # Число клиентов на сообщение
    chunk_size = 50
    for i in range(0, len(stats), chunk_size):
        chunk = stats[i : i + chunk_size]
        body = []
        for row in chunk:
            email = str(row.get("email", ""))
            # Обрезаем длинные email для сохранения верстки
            if len(email) > 15:
                email = email[:12] + "..."

            up = int(row.get("up") or row.get("total_up") or 0)
            down = int(row.get("down") or row.get("total_down") or 0)

            body.append(f"{email:<15} {format_bytes(up):<9} {format_bytes(down):<9}")

        message = header + "\n".join(body) + "\n" + footer
        try:
            # Отправляем отчёт администратору
            await bot.send_message(chat_id=config.admin_id, text=message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send report chunk {i}: {e}")


async def daily_job(bot: Bot):
    """Ежедневная задача: сохраняет снимок, шлёт отчёты и сбрасывает счётчики."""
    logger.info("Running daily traffic report job")

    # Получаем статистику трафика из X-UI за предыдущий день
    stats = db.get_xui_traffic_stats()
    if stats:
        report_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        # Сохраняем снимки статистики для каждого клиента
        for row in stats:
            db.save_traffic_snapshot(
                str(row["email"]), int(row.get("up", 0)), int(row.get("down", 0)), report_date
            )

        # Отправляем ежедневный отчёт
        await send_report(bot, f"Отчёт за день ({report_date})", stats)

        # Сбрасываем счетчики трафика в X-UI
        db.reset_xui_traffic()
    else:
        logger.info("No data for daily report (no traffic)")

    # Получаем статистику трафика из БД за предыдущий день
    today = datetime.now()
    yesterday = today - timedelta(days=1)

    if today.weekday() == 0:
        # Формируем недельный отчёт если сегодня понедельник
        logger.info("Generating weekly report")
        end_date = yesterday.strftime("%Y-%m-%d")
        start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        weekly_stats = db.get_traffic_stats(start_date, end_date)
        if weekly_stats:
            await send_report(bot, f"Недельный отчёт ({start_date} – {end_date})", weekly_stats)

    if today.day == 1:
        # Формируем месячный отчёт если сегодня первое число месяца
        logger.info("Generating monthly report")

        # Получаем статистику трафика из БД за предыдущий месяц
        last_month_end = yesterday
        last_month_start = last_month_end.replace(day=1)
        start_str = last_month_start.strftime("%Y-%m-%d")
        end_str = last_month_end.strftime("%Y-%m-%d")
        monthly_stats = db.get_traffic_stats(start_str, end_str)

        # Отправляем месячный отчёт
        if monthly_stats:
            title = f"Месячный отчёт ({last_month_start.strftime('%B %Y')})"
            await send_report(bot, title, monthly_stats)


def start_scheduler(bot: Bot):
    """
    Запускает AsyncIO планировщик задач.
    Настраивает ежедневный запуск daily_job в 00:01.
    """
    # Добавляем задачу: каждый день в 00:01
    scheduler.add_job(daily_job, "cron", hour=0, minute=1, args=[bot])

    scheduler.start()
    logger.info("Scheduler started")
