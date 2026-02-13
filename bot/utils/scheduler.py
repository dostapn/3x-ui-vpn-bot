"""
Планировщик задач: ежедневные/недельные/месячные отчёты по трафику all_time.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from bot.config import config
from bot.database import db
from bot.utils.messages import format_bytes

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

CHUNK_SIZE = 30  # Число клиентов на сообщение


def _truncate_email(email: str, max_len: int = 14, ellipsis_over: Optional[int] = None) -> str:
    """Обрезает email для колонки Профиль. ellipsis_over: если len>N, то max_len-3 + '...'."""
    s = str(email or "")
    if ellipsis_over is not None and len(s) > ellipsis_over:
        return s[: max_len - 3] + "..."
    return s[:max_len]


def _format_row_daily(row: Dict[str, Any], is_first: bool) -> str:
    """Строка для ежедневного отчёта: Профиль | Всего | Δ вчера | Активность."""

    # Колонка: Профиль
    email = _truncate_email(row.get("email", ""), max_len=14)
    # Колонка: Всего
    all_time = format_bytes(int(row.get("all_time", 0)))
    # Колонка: Δ вчера
    has_prev, delta, prev = row.get("has_prev", False), row.get("delta"), row.get("prev_all_time")
    if not has_prev or delta is None:
        delta_str = "0.00 GB (вчера: −)"
    else:
        prev_str = format_bytes(prev) if prev else "−"
        delta_str = f"{format_bytes(delta)} (было: {prev_str})"
    # Колонка: Активность
    if is_first:
        act = "показаний нет"
    else:
        total, active, consec = (
            row.get("total_days", 0),
            row.get("active_days", 0),
            row.get("consecutive_inactive", 0),
        )
        act = (
            f"0 из {total}: неактивен {consec} дн. подряд"
            if consec > 0
            else f"{active} из {total}" if total else "показаний нет"
        )

    return f"{email:<14} {all_time:<12} {delta_str:<28} {act}"


def _format_row_period(row: Dict[str, Any]) -> str:
    """Строка для отчёта за период: Профиль | Всего | За период | Активность."""

    # Колонка: Профиль
    email = _truncate_email(row.get("email", ""), max_len=14, ellipsis_over=14)
    # Колонка: Всего
    all_time = format_bytes(int(row.get("all_time", 0)))
    # Колонка: За период
    period = format_bytes(int(row.get("period_traffic", 0)))
    # Колонка: Активность
    active, total = row.get("active_days", 0), row.get("period_days", 0)
    act_str = f"{active} из {total} дн." if total > 0 else "—"

    return f"{email:<14} {all_time:<12} {period:<14} {act_str}"


async def _send_report_daily(bot: Bot) -> None:
    """Ежедневный отчёт: all_time, delta, активность."""
    report_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

    rows = db.get_all_time_report_data(report_date, prev_date)
    db.backfill_daily_report(report_date, rows)

    if not rows:
        logger.info("No clients for daily report")
        return

    is_first = not any(r.get("has_prev") for r in rows)
    title = f"Трафик all_time — {report_date}"
    await _send_in_chunks(
        bot,
        title,
        rows,
        f"{'Профиль':<14} {'Всего':<12} {'Δ вчера':<28} {'Активность'}",
        lambda r: _format_row_daily(r, is_first),
    )


async def _send_report_period(bot: Bot, period_days: int, label: str) -> None:
    """Отчёт за период: недельный (7) или месячный (30)."""
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    start_date = (today - timedelta(days=period_days)).strftime("%Y-%m-%d")
    end_date = yesterday.strftime("%Y-%m-%d")

    rows = db.get_period_report_data(start_date, end_date, period_days)
    if not rows:
        return

    title = f"{label} отчёт ({start_date} – {end_date})"
    await _send_in_chunks(
        bot,
        title,
        rows,
        f"{'Профиль':<14} {'Всего':<12} {'За период':<14} {'Активность'}",
        _format_row_period,
    )


async def _send_in_chunks(
    bot: Bot,
    title: str,
    rows: List[Dict[str, Any]],
    columns: str,
    format_row: Callable[[Dict[str, Any]], str],
) -> None:
    """Общая отправка отчёта админу чанками."""
    if not rows:
        return

    header = f"📊 <b>{title}</b>\n\n<pre>{columns}\n{'-' * 70}\n"
    footer = f"</pre>\n📅 <i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"

    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        body = "\n".join(format_row(r) for r in chunk)
        message = header + body + "\n" + footer
        try:
            await bot.send_message(chat_id=config.admin_id, text=message, parse_mode="HTML")
        except Exception as e:
            logger.error("Failed to send report chunk %s: %s", i, e)


async def daily_job(bot: Bot) -> None:
    """Ежедневная задача: backfill, daily, weekly (если пн), monthly (если 1-е)."""
    logger.info("Running daily traffic report job")

    await _send_report_daily(bot)

    today = datetime.now()
    if today.weekday() == 0:
        await _send_report_period(bot, 7, "Недельный")
    if today.day == 1:
        await _send_report_period(bot, 30, "Месячный")


def start_scheduler(bot: Bot) -> None:
    """Запускает планировщик: ежедневный запуск daily_job в 00:01."""
    if not scheduler.running:
        scheduler.add_job(daily_job, "cron", hour=0, minute=1, args=[bot])
        scheduler.start()
        logger.info("Scheduler started")
