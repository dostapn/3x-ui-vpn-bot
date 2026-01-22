"""
Обработчики клиентской части VPN бота
Обрабатывает /start, просмотр ключей и запросы на ключи
"""

import logging
import uuid
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from bot.database import db
from bot.utils.xui_api import xui_api
from bot.utils.keyboards import (
    get_main_keyboard,
    get_admin_request_keyboard,
    get_key_actions_keyboard,
    get_back_to_menu_keyboard,
)
from bot.utils.messages import format_key_info_message, get_connection_instructions

logger = logging.getLogger(__name__)

# Создаём роутер для клиентских обработчиков
client_router = Router()


@client_router.message(Command("start"))
async def cmd_start(message: Message):
    """
    Обработка команды /start
    - Сохранение пользователя в БД
    - Проверка блокировки пользователя
    - Показ главного меню с доступными действиями
    """
    tg_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name or "User"
    last_name = message.from_user.last_name

    # Save user to database
    db.save_user(tg_id, username, first_name, last_name)

    # Check if user is blocked
    if db.is_user_blocked(tg_id):
        await message.answer(
            "⛔ Вы заблокированы на 24 часа.\n"
            "Попробуйте позже или свяжитесь с администратором."
        )
        logger.warning(f"Blocked user {tg_id} (@{username}) attempted to use bot")
        return

    # Check if user has any keys
    user_keys = db.get_user_keys(tg_id)
    has_keys = len(user_keys) > 0

    # Welcome message
    welcome_text = f"👋 Привет, {first_name}!\n\n"

    if has_keys:
        welcome_text += f"🔑 У вас есть {len(user_keys)} ключ(ей)\n\n"
    else:
        welcome_text += "❌ У вас пока нет ключей\n\n"

    welcome_text += (
        "Используйте кнопки ниже для управления VPN-ключами:\n\n"
        "🔑 <b>Мои ключи</b> — просмотр ваших активных ключей\n"
        "➕ <b>Запросить новый ключ</b> — отправить запрос администратору\n\n"
        "💬 Есть вопрос? Просто напишите сообщение — администратор получит его."
    )

    await message.answer(
        welcome_text, reply_markup=get_main_keyboard(has_keys), parse_mode="HTML"
    )

    logger.info(f"User {tg_id} (@{username}) started bot")


@client_router.message(Command("help"))
async def cmd_help(message: Message):
    """Показать справочную информацию"""
    from bot.config import config

    is_admin = message.from_user.id == config.admin_id

    help_text = (
        "📖 <b>Справка по боту</b>\n\n"
        "<b>Команды:</b>\n"
        "/start — Главное меню\n"
        "/help — Эта справка\n"
        "/status — Статус бота\n"
        "/id — Ваш Telegram ID\n"
    )

    if is_admin:
        help_text += (
            "\n<b>🔧 Админские команды:</b>\n"
            "/requests — Список незавершённых запросов\n"
            "/keys — Список всех ключей\n"
            "/bans — Список заблокированных\n"
            "/unban_ID — Разблокировать пользователя\n"
        )

    help_text += (
        "\n<b>Как использовать:</b>\n"
        "1️⃣ Запросите ключ через кнопку\n"
        "2️⃣ Дождитесь одобрения\n"
        "3️⃣ Получите VLESS конфиг\n"
        "4️⃣ Импортируйте в v2rayNG / Hiddify\n\n"
        "💬 Вопросы? Напишите текстом."
    )

    await message.answer(help_text, parse_mode="HTML")
    logger.info(f"User {message.from_user.id} requested help")


@client_router.message(Command("status"))
async def cmd_status(message: Message):
    """Проверить статус бота"""
    tg_id = message.from_user.id

    # Check if user is blocked
    if db.is_user_blocked(tg_id):
        status_text = (
            "⛔ <b>Статус: Заблокирован</b>\n\n"
            "Вы временно заблокированы на 24 часа.\n"
            "Свяжитесь с администратором для разблокировки."
        )
    else:
        user_keys = db.get_user_keys(tg_id)
        pending_requests = db.get_pending_requests_by_user(tg_id)

        status_text = (
            "✅ <b>Статус: Активен</b>\n\n"
            f"🔑 Активных ключей: {len(user_keys)}\n"
            f"⏳ Ожидающих запросов: {len(pending_requests)}\n\n"
            "Все системы работают нормально!"
        )

    await message.answer(status_text, parse_mode="HTML")
    logger.info(f"User {tg_id} checked status")


@client_router.message(Command("id"))
async def cmd_id(message: Message):
    """Показать Telegram ID пользователя"""
    tg_id = message.from_user.id
    username = message.from_user.username or "не установлен"
    first_name = message.from_user.first_name or "User"

    id_text = (
        f"🆔 <b>Ваш Telegram ID</b>\n\n"
        f"ID: <code>{tg_id}</code>\n"
        f"Имя: {first_name}\n"
        f"Username: @{username}\n\n"
        f"💡 Скопируйте ID для связи с администратором"
    )

    await message.answer(id_text, parse_mode="HTML")
    logger.info(f"User {tg_id} requested their ID")


@client_router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery):
    """Вернуться в главное меню"""
    tg_id = callback.from_user.id
    first_name = callback.from_user.first_name or "User"

    user_keys = db.get_user_keys(tg_id)
    has_keys = len(user_keys) > 0

    text = f"👋 {first_name}\n\n"
    if has_keys:
        text += f"🔑 У вас есть {len(user_keys)} ключ(ей)"
    else:
        text += "❌ У вас пока нет ключей"

    await callback.message.edit_text(text, reply_markup=get_main_keyboard(has_keys))
    await callback.answer()


@client_router.callback_query(F.data == "get_keys")
async def callback_get_keys(callback: CallbackQuery):
    """
    Показать ключи пользователя с конфигами и subscription URL
    """
    tg_id = callback.from_user.id

    # Get user's keys from database
    user_keys = db.get_user_keys(tg_id)

    if not user_keys:
        await callback.answer(
            "❌ У вас нет ключей. Запросите новый ключ!", show_alert=True
        )
        return

    await callback.message.edit_text(
        f"🔑 <b>Ваши ключи ({len(user_keys)}):</b>\n\n"
        "Отправляю информацию по каждому ключу...",
        parse_mode="HTML",
    )

    # Send info for each key
    for key in user_keys:
        email = key["client_email"]
        inbound_id = key["inbound_id"]
        comment = key["comment"] or "Без комментария"

        # Get client info from 3x-ui
        client_info = xui_api.find_client_by_email(email)

        if not client_info:
            await callback.message.answer(
                f"⚠️ Ключ <code>{email}</code> не найден в системе", parse_mode="HTML"
            )
            continue

        client = client_info["client"]
        inbound_remark = client_info["inbound_remark"]

        # Format traffic info
        if client.total_gb > 0:
            total_gb = client.total_gb / (1024**3)
            used_gb = (
                (client.up + client.down) / (1024**3) if hasattr(client, "up") else 0
            )
            traffic_info = f"📊 Трафик: {used_gb:.2f} / {total_gb:.2f} GB"
        else:
            traffic_info = "📊 Трафик: безлимит"

        # Format expiry info
        if client.expiry_time > 0:
            expiry_date = datetime.fromtimestamp(client.expiry_time / 1000)
            expiry_info = f"⏰ До: {expiry_date.strftime('%d.%m.%Y %H:%M')}"
        else:
            expiry_info = "⏰ Срок: бессрочно"

        # Status
        status = "✅ Активен" if client.enable else "❌ Отключен"

        # Get VLESS config URL
        vless_url = xui_api.get_client_config(inbound_id, email)

        # Format message using centralized template
        key_text = format_key_info_message(
            email=email,
            comment=comment,
            inbound_remark=inbound_remark,
            status=status,
            traffic_info=traffic_info,
            expiry_info=expiry_info,
            vless_url=vless_url,
        )

        await callback.message.answer(
            key_text, parse_mode="HTML", reply_markup=get_key_actions_keyboard(email)
        )

    # Show connection instructions and back button
    final_message = (
        "✅ <b>Все ключи показаны</b>\n\n" f"{get_connection_instructions()}"
    )

    await callback.message.answer(
        final_message, parse_mode="HTML", reply_markup=get_back_to_menu_keyboard()
    )

    await callback.answer()
    logger.info(f"User {tg_id} viewed their keys")


@client_router.callback_query(F.data == "request_key")
async def callback_request_key(callback: CallbackQuery):
    """
    Обработка запроса ключа от клиента
    - Проверка блокировки пользователя
    - Поиск или создание незавершённого запроса (без дубликатов)
    - Отправка уведомления админу с кнопками действий
    """
    tg_id = callback.from_user.id
    username = callback.from_user.username or "no_username"
    first_name = callback.from_user.first_name or "User"
    last_name = callback.from_user.last_name

    # Check if user is blocked
    if db.is_user_blocked(tg_id):
        await callback.answer("⛔ Вы заблокированы на 24 часа", show_alert=True)
        return

    # Check if user already has a pending request
    existing_requests = db.get_pending_requests_by_user(tg_id)

    if existing_requests:
        # Use the most recent request
        request = existing_requests[0]
        request_id = request["request_id"]

        await callback.answer("ℹ️ У вас уже есть активный запрос")
        created_time = datetime.fromtimestamp(request["created_at"]).strftime(
            "%d.%m.%Y %H:%M"
        )
        await callback.message.edit_text(
            "⏳ <b>Запрос уже отправлен</b>\n\n"
            "Администратор рассмотрит ваш запрос и примет решение.\n"
            "Вы получите уведомление, когда ключ будет готов.\n\n"
            f"📅 Запрос создан: {created_time}",
            reply_markup=get_back_to_menu_keyboard(),
            parse_mode="HTML",
        )
        logger.info(f"User {tg_id} (@{username}) tried to create duplicate request")
        return

    # Generate unique request ID
    request_id = str(uuid.uuid4())

    # Save pending request to database
    db.create_pending_request(
        request_id=request_id,
        tg_id=tg_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )

    # First, notify the client
    await callback.answer("✅ Запрос отправлен администратору")
    await callback.message.edit_text(
        "⏳ <b>Запрос отправлен</b>\n\n"
        "Администратор рассмотрит ваш запрос и примет решение.\n"
        "Вы получите уведомление, когда ключ будет готов.",
        parse_mode="HTML",
        reply_markup=get_back_to_menu_keyboard(),
    )

    logger.info(f"User {tg_id} (@{username}) requested new key: {request_id}")

    # Then, send notification to admin
    admin_text = "🔑 <b>Новый запрос на ключ</b>\n\n" f"👤 Имя: {first_name}"

    if last_name:
        admin_text += f" {last_name}"

    admin_text += (
        f"\n🆔 Telegram ID: <code>{tg_id}</code>\n"
        f"👤 Username: @{username}\n\n"
        f"🆔 Request ID: <code>{request_id}</code>"
    )

    from aiogram import Bot
    from bot.config import config

    bot = Bot(token=config.bot_token)

    try:
        await bot.send_message(
            chat_id=config.admin_id,
            text=admin_text,
            reply_markup=get_admin_request_keyboard(request_id),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Failed to send admin notification: {e}")
    finally:
        await bot.session.close()


@client_router.callback_query(F.data.startswith("qr_"))
async def callback_qr_code(callback: CallbackQuery):
    """Генерация QR-кода для ключа (заглушка для будущей реализации)"""
    email = callback.data.replace("qr_", "")

    await callback.answer(
        "📱 QR-код будет добавлен в следующей версии", show_alert=True
    )
    logger.debug(f"QR code requested for {email}")


@client_router.callback_query(F.data.startswith("stats_"))
async def callback_stats(callback: CallbackQuery):
    """Показать детальную статистику для ключа"""
    email = callback.data.replace("stats_", "")

    stats = xui_api.get_client_stats(email)

    if not stats:
        await callback.answer("❌ Статистика недоступна", show_alert=True)
        return

    # Format traffic
    up_gb = stats["up"] / (1024**3)
    down_gb = stats["down"] / (1024**3)
    total_gb = stats["total"] / (1024**3) if stats["total"] > 0 else 0

    stats_text = (
        f"📊 <b>Статистика: {email}</b>\n\n"
        f"🖥 Inbound: {stats['inbound_remark']}\n"
        f"📤 Отправлено: {up_gb:.2f} GB\n"
        f"📥 Получено: {down_gb:.2f} GB\n"
    )

    if total_gb > 0:
        stats_text += f"📊 Лимит: {total_gb:.2f} GB\n"
    else:
        stats_text += "📊 Лимит: безлимит\n"

    if stats["expiry_time"] > 0:
        expiry = datetime.fromtimestamp(stats["expiry_time"] / 1000)
        stats_text += f"⏰ Истекает: {expiry.strftime('%d.%m.%Y %H:%M')}\n"
    else:
        stats_text += "⏰ Срок: бессрочно\n"

    status = "✅ Активен" if stats["enable"] else "❌ Отключен"
    stats_text += f"\n{status}"

    await callback.message.answer(
        stats_text, parse_mode="HTML", reply_markup=get_back_to_menu_keyboard()
    )

    await callback.answer()
    logger.info(f"Stats shown for {email}")


@client_router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message):
    """
    Обработка текстовых сообщений от пользователя
    Пересылает сообщение администратору
    """
    from aiogram import Bot
    from bot.config import config

    tg_id = message.from_user.id
    username = message.from_user.username or "без username"
    first_name = message.from_user.first_name or "User"

    # Проверяем, не является ли отправитель администратором
    if tg_id == config.admin_id:
        logger.debug(f"Ignoring message from admin {tg_id}")
        return

    # Проверяем, не заблокирован ли пользователь
    if db.is_user_blocked(tg_id):
        await message.answer(
            "⛔️ Вы заблокированы и не можете отправлять сообщения.",
            reply_markup=get_main_keyboard(),
        )
        return

    # Сохраняем пользователя в БД
    db.save_user(
        tg_id=tg_id,
        username=username,
        first_name=first_name,
        last_name=message.from_user.last_name,
    )

    # Отправляем сообщение админу
    bot = Bot(token=config.bot_token)

    try:
        # Формируем текст сообщения
        message_text = (
            f"💬 <b>Сообщение от пользователя</b>\n\n"
            f"👤 От: {first_name} (@{username})\n"
            f"🆔 ID: <code>{tg_id}</code>\n\n"
        )

        # Если это reply, добавляем цитату
        if message.reply_to_message and message.reply_to_message.text:
            quoted_text = message.reply_to_message.text
            # Ограничиваем длину цитаты
            if len(quoted_text) > 100:
                quoted_text = quoted_text[:100] + "..."
            message_text += (
                f"💭 <i>В ответ на:</i>\n<blockquote>{quoted_text}</blockquote>\n\n"
            )

        message_text += f"📝 Текст:\n{message.text}"

        # Пересылаем сообщение админу с информацией об отправителе
        await bot.send_message(
            chat_id=config.admin_id,
            text=message_text,
            parse_mode="HTML",
            reply_markup=None,  # Админ может просто ответить reply
        )

        # Подтверждаем пользователю
        await message.answer(
            "✅ Ваше сообщение отправлено администратору",
            reply_markup=get_main_keyboard(),
        )

        logger.info(f"Message from user {tg_id} forwarded to admin")

    except Exception as e:
        logger.error(f"Failed to forward message to admin: {e}")
        await message.answer(
            "❌ Ошибка отправки сообщения. Попробуйте позже.",
            reply_markup=get_main_keyboard(),
        )
    finally:
        await bot.session.close()
