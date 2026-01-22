"""
Обработчики администратора для VPN бота
Управляет одобрением запросов на ключи, присвоением, отклонением и обменом сообщениями
"""

import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import config
from bot.database import db
from bot.utils.xui_api import xui_api
from bot.utils.keyboards import (
    get_inbound_selection_keyboard,
    get_template_inbound_keyboard,
    get_client_list_keyboard,
    get_admin_request_keyboard,
)
from bot.utils.messages import format_vless_config_message

logger = logging.getLogger(__name__)

# Создаем роутер для обработчиков администратора
admin_router = Router()


class AdminStates(StatesGroup):
    """FSM состояния для операций администратора"""

    waiting_ask_message = State()
    waiting_assign_email = State()
    waiting_new_inbound_port = State()
    waiting_new_inbound_remark = State()


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id == config.admin_id


# ===== Одобрение запроса (создание нового ключа) =====


@admin_router.callback_query(F.data.startswith("accept_"))
async def callback_accept_request(callback: CallbackQuery):
    """
    Обработка кнопки "Одобрить" - показ выбора inbound
    """
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Только для администратора", show_alert=True)
        return

    request_id = callback.data.replace("accept_", "")

    # Проверяем, существует ли запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден или уже обработан", show_alert=True)
        return

    # Получаем доступные inbound'ы
    inbounds = xui_api.get_all_inbounds()

    if not inbounds:
        await callback.answer(
            "❌ Нет доступных inbound'ов. Создайте через 3x-ui панель.", show_alert=True
        )
        return

    await callback.message.edit_text(
        f"🔑 <b>Выдача ключа для:</b>\n"
        f"👤 {request['first_name']} (@{request['username']})\n\n"
        f"Выберите inbound или создайте новый:",
        reply_markup=get_inbound_selection_keyboard(request_id, inbounds),
        parse_mode="HTML",
    )

    await callback.answer()


@admin_router.callback_query(F.data.startswith("select_inbound_"))
async def callback_select_inbound(callback: CallbackQuery):
    """
    Обработка выбора inbound - создание клиента в выбранном inbound
    """
    if not is_admin(callback.from_user.id):
        return

    # Парсим callback data: select_inbound_{request_id}_{inbound_id}
    parts = callback.data.split("_")
    request_id = parts[2]
    inbound_id = int(parts[3])

    # Получаем запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    tg_id = request["tg_id"]
    username = request["username"]

    # Генерируем email для клиента
    email = f"tg_{tg_id}_{username}"

    # Создаем клиента в 3x-ui
    await callback.message.edit_text("⏳ Создаю ключ...", parse_mode="HTML")

    client = xui_api.create_client(
        inbound_id=inbound_id,
        email=email,
        total_gb=0,  # Безлимит (используем настройки 3x-ui по умолчанию)
        expiry_time=0,  # Бессрочно
        enable=True,
    )

    if not client:
        await callback.answer("❌ Ошибка создания ключа", show_alert=True)
        await callback.message.edit_text(
            "❌ <b>Ошибка создания ключа</b>\n\n"
            "Проверьте логи 3x-ui или попробуйте другой inbound.",
            parse_mode="HTML",
            reply_markup=get_admin_request_keyboard(request_id),
        )
        return

    # Сохраняем в базу данных
    db.add_user_key(
        tg_id=tg_id,
        client_email=email,
        inbound_id=inbound_id,
        comment=f"Выдан {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )

    # Получаем VLESS конфиг
    vless_url = xui_api.get_client_config(inbound_id, email)

    # Получаем информацию об inbound
    inbound = xui_api.get_inbound(inbound_id)
    inbound_name = inbound.remark if inbound else f"Inbound {inbound_id}"

    # Отправляем ключ пользователю
    from aiogram import Bot

    bot = Bot(token=config.bot_token)

    try:
        # Форматируем сообщение используя централизованный шаблон
        user_message = format_vless_config_message(
            email=email,
            inbound_name=inbound_name,
            vless_url=vless_url,
            title="✅ <b>Ваш ключ готов!</b>",
        )

        await bot.send_message(chat_id=tg_id, text=user_message, parse_mode="HTML")

        # Подтверждаем админу
        await callback.message.edit_text(
            f"✅ <b>Ключ выдан</b>\n\n"
            f"👤 Пользователь: {request['first_name']} (@{username})\n"
            f"🔑 Email: <code>{email}</code>\n"
            f"🖥 Inbound: {inbound_name}\n\n"
            f"Пользователь получил уведомление с ключом.",
            parse_mode="HTML",
        )

        # Удаляем обработанный запрос
        db.delete_pending_request(request_id)

        await callback.answer("✅ Ключ успешно выдан")
        logger.info(
            f"Admin created key {email} for user {tg_id} in inbound {inbound_id}"
        )

    except Exception as e:
        logger.error(f"Failed to send key to user {tg_id}: {e}")
        await callback.answer(
            "⚠️ Ключ создан, но не удалось отправить пользователю", show_alert=True
        )
    finally:
        await bot.session.close()


@admin_router.callback_query(F.data.startswith("create_inbound_"))
async def callback_create_inbound(callback: CallbackQuery, state: FSMContext):
    """
    Обработка создания нового inbound - показ выбора шаблона
    """
    if not is_admin(callback.from_user.id):
        return

    request_id = callback.data.replace("create_inbound_", "")

    # Получаем существующие inbound'ы для использования в качестве шаблонов
    inbounds = xui_api.get_all_inbounds()

    if not inbounds:
        await callback.answer(
            "❌ Нет inbound'ов для клонирования. Создайте первый через панель 3x-ui.",
            show_alert=True,
        )
        return

    # Сохраняем request_id в состояние
    await state.update_data(request_id=request_id)

    await callback.message.edit_text(
        "📋 <b>Создание нового inbound</b>\n\n"
        "Выберите существующий inbound как шаблон для клонирования настроек:",
        reply_markup=get_template_inbound_keyboard(request_id, inbounds),
        parse_mode="HTML",
    )

    await callback.answer()


# ===== Присвоение существующего ключа =====


@admin_router.callback_query(
    F.data.startswith("assign_")
    & ~F.data.startswith("assign_inbound_")
    & ~F.data.startswith("assign_client_")
)
async def callback_assign_request(callback: CallbackQuery):
    """
    Обработка кнопки "Присвоить" - показ списка inbound'ов для выбора
    """
    logger.info(f"callback_assign_request called with data: {callback.data}")

    if not is_admin(callback.from_user.id):
        logger.warning(
            f"Non-admin user {callback.from_user.id} tried to access admin function"
        )
        return

    request_id = callback.data.replace("assign_", "")
    logger.info(f"Extracted request_id: {request_id}")

    # Проверяем, существует ли запрос
    request = db.get_pending_request(request_id)
    logger.info(f"Request found: {request is not None}")

    if not request:
        logger.error(f"Request {request_id} not found in database")
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    # Отвечаем на callback немедленно, чтобы предотвратить таймаут
    await callback.answer("⏳ Загружаю список inbound'ов...")

    # Редактируем сообщение для отображения загрузки
    await callback.message.edit_text(
        "⏳ <b>Загрузка списка inbound'ов...</b>\n\nПожалуйста, подождите.",
        parse_mode="HTML",
    )

    # Получаем все inbound'ы (это может занять время)
    inbounds = xui_api.get_all_inbounds()

    if not inbounds:
        await callback.message.edit_text(
            "❌ <b>Нет доступных inbound'ов</b>\n\n"
            "Создайте inbound через панель 3x-ui.",
            parse_mode="HTML",
            reply_markup=get_admin_request_keyboard(request_id),
        )
        return

    await callback.message.edit_text(
        f"🔄 <b>Присвоение существующего ключа</b>\n\n"
        f"👤 Пользователь: {request['first_name']} (@{request['username']})\n\n"
        f"Шаг 1: Выберите inbound для просмотра клиентов:",
        reply_markup=get_inbound_selection_keyboard(
            request_id, inbounds, prefix="assign_inbound", show_create_new=False
        ),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("assign_inbound_"))
async def callback_assign_select_inbound(callback: CallbackQuery):
    """
    Обработка выбора inbound для присвоения - показ клиентов из выбранного inbound
    """
    if not is_admin(callback.from_user.id):
        return

    logger.info(f"Received assign_inbound callback: {callback.data}")

    # Парсим: assign_inbound_{request_id}_{inbound_id}
    # Удаляем префикс, получаем: {request_id}_{inbound_id}
    data = callback.data.replace("assign_inbound_", "")

    logger.debug(f"After removing prefix: {data}")

    # Разделяем с конца, чтобы получить inbound_id (последняя часть после последнего подчеркивания)
    parts = data.rsplit("_", 1)
    logger.debug(f"Split parts: {parts}")

    if len(parts) != 2:
        logger.error(f"Invalid callback data format: {callback.data}")
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    request_id = parts[0]
    inbound_id = int(parts[1])

    logger.info(f"Parsed: request_id={request_id}, inbound_id={inbound_id}")

    # Проверяем, существует ли запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    # Получаем клиентов для этого inbound
    clients = xui_api.get_clients_by_inbound(inbound_id)

    if not clients:
        await callback.answer("❌ В этом inbound нет клиентов", show_alert=True)
        return

    # Получаем информацию об inbound
    inbound = xui_api.get_inbound(inbound_id)
    inbound_name = inbound.remark if inbound else f"Inbound {inbound_id}"

    # Подготавливаем список клиентов с информацией об inbound
    clients_with_info = [
        {
            "client": client,
            "inbound_id": inbound_id,
            "inbound_remark": inbound_name,
            "inbound_port": inbound.port if inbound else 0,
        }
        for client in clients
    ]

    await callback.message.edit_text(
        f"🔄 <b>Присвоение существующего ключа</b>\n\n"
        f"👤 Пользователь: {request['first_name']} (@{request['username']})\n"
        f"🖥 Inbound: {inbound_name}\n\n"
        f"Шаг 2: Выберите клиента ({len(clients)} доступно):",
        reply_markup=get_client_list_keyboard(clients_with_info, request_id),
        parse_mode="HTML",
    )

    await callback.answer()


@admin_router.callback_query(F.data.startswith("assign_client_"))
async def callback_assign_client(callback: CallbackQuery):
    """
    Обработка выбора клиента для присвоения
    """
    if not is_admin(callback.from_user.id):
        return

    # Парсим: assign_client_{request_id}_{email}
    parts = callback.data.split("_", 3)
    request_id = parts[2]
    email = parts[3]

    # Получаем запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    tg_id = request["tg_id"]

    # Находим клиента
    client_info = xui_api.find_client_by_email(email)
    if not client_info:
        await callback.answer("❌ Ключ не найден", show_alert=True)
        return

    inbound_id = client_info["inbound_id"]
    inbound_remark = client_info["inbound_remark"]

    # Добавляем в базу данных
    db.add_user_key(
        tg_id=tg_id,
        client_email=email,
        inbound_id=inbound_id,
        comment=f"Присвоен {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )

    # Получаем VLESS конфиг
    vless_url = xui_api.get_client_config(inbound_id, email)

    # Отправляем пользователю
    from aiogram import Bot

    bot = Bot(token=config.bot_token)

    try:
        # Форматируем сообщение используя централизованный шаблон
        user_message = format_vless_config_message(
            email=email,
            inbound_name=inbound_remark,
            vless_url=vless_url,
            title="✅ <b>Вам присвоен ключ!</b>",
        )

        await bot.send_message(chat_id=tg_id, text=user_message, parse_mode="HTML")

        # Подтверждаем админу
        await callback.message.edit_text(
            f"✅ <b>Ключ присвоен</b>\n\n"
            f"👤 Пользователь: {request['first_name']} (@{request['username']})\n"
            f"🔑 Email: <code>{email}</code>\n"
            f"🖥 Inbound: {inbound_remark}\n\n"
            f"Пользователь получил уведомление.",
            parse_mode="HTML",
        )

        # Удаляем обработанный запрос
        db.delete_pending_request(request_id)

        await callback.answer("✅ Ключ присвоен")
        logger.info(f"Admin assigned key {email} to user {tg_id}")

    except Exception as e:
        logger.error(f"Failed to send key to user {tg_id}: {e}")
        await callback.answer(
            "⚠️ Ключ присвоен, но не удалось отправить", show_alert=True
        )
    finally:
        await bot.session.close()


# ===== Отклонение запроса (без блокировки) =====


@admin_router.callback_query(F.data.startswith("reject_"))
async def callback_reject_request(callback: CallbackQuery):
    """
    Обработка кнопки "Отклонить" - простое отклонение запроса без блокировки
    """
    if not is_admin(callback.from_user.id):
        return

    request_id = callback.data.replace("reject_", "")

    # Получаем запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    tg_id = request["tg_id"]
    username = request["username"]

    # Отправляем уведомление пользователю
    from aiogram import Bot

    bot = Bot(token=config.bot_token)

    try:
        user_message = (
            "❌ <b>Ваш запрос на ключ отклонен</b>\n\n"
            "К сожалению, администратор отклонил ваш запрос.\n"
            "Вы можете создать новый запрос позже."
        )
        await bot.send_message(chat_id=tg_id, text=user_message, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send rejection notification to user {tg_id}: {e}")
    finally:
        await bot.session.close()

    # Удаляем запрос (без блокировки)
    db.delete_pending_request(request_id)

    await callback.message.edit_text(
        f"❌ <b>Запрос отклонен</b>\n\n"
        f"👤 Пользователь: {request['first_name']} (@{username})\n"
        f"🆔 ID: {tg_id}\n\n"
        f"Запрос удален без блокировки.\n"
        f"Пользователь получил уведомление.",
        parse_mode="HTML",
    )

    await callback.answer("✅ Запрос отклонен")
    logger.info(
        f"Admin rejected request from user {tg_id} (@{username}) without blocking"
    )


# ===== Отклонение запроса (с блокировкой) =====


@admin_router.callback_query(F.data.startswith("denied_"))
async def callback_deny_request(callback: CallbackQuery):
    """
    Обработка кнопки "Отклонить с блокировкой" - блокировка пользователя на 24 часа без уведомления
    """
    if not is_admin(callback.from_user.id):
        return

    request_id = callback.data.replace("denied_", "")

    # Получаем запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    tg_id = request["tg_id"]
    username = request["username"]

    # Блокируем пользователя на 24 часа
    db.block_user(tg_id, hours=24)

    # Удаляем запрос
    db.delete_pending_request(request_id)

    await callback.message.edit_text(
        f"❌ <b>Запрос отклонен</b>\n\n"
        f"👤 Пользователь: {request['first_name']} (@{username})\n"
        f"🆔 ID: {tg_id}\n\n"
        f"⛔ Пользователь заблокирован на 24 часа\n"
        f"(без уведомления)",
        parse_mode="HTML",
    )

    await callback.answer("✅ Пользователь заблокирован")
    logger.warning(f"Admin denied request and blocked user {tg_id} (@{username})")


# ===== Отправка сообщения пользователю =====


@admin_router.callback_query(F.data.startswith("ask_"))
async def callback_ask_user(callback: CallbackQuery, state: FSMContext):
    """
    Обработка кнопки "Написать" - переход в FSM для получения сообщения для пользователя
    """
    if not is_admin(callback.from_user.id):
        return

    request_id = callback.data.replace("ask_", "")

    # Проверяем, существует ли запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    # Сохраняем в FSM состояние
    await state.update_data(request_id=request_id, target_user_id=request["tg_id"])
    await state.set_state(AdminStates.waiting_ask_message)

    await callback.message.edit_text(
        f"💬 <b>Отправка сообщения пользователю</b>\n\n"
        f"👤 {request['first_name']} (@{request['username']})\n\n"
        f"Напишите сообщение, которое будет отправлено пользователю от имени бота.\n\n"
        f"Запрос останется активным после отправки сообщения.",
        parse_mode="HTML",
    )

    await callback.answer()


@admin_router.message(AdminStates.waiting_ask_message)
async def process_ask_message(message: Message, state: FSMContext):
    """
    Обработка сообщения от админа для отправки пользователю
    """
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    request_id = data.get("request_id")

    if not target_user_id or not request_id:
        await message.answer("❌ Ошибка: данные запроса потеряны")
        await state.clear()
        return

    # Получаем информацию о запросе
    request = db.get_pending_request(request_id)
    if not request:
        await message.answer("❌ Запрос больше не существует")
        await state.clear()
        return

    # Отправляем сообщение пользователю
    from aiogram import Bot

    bot = Bot(token=config.bot_token)

    try:
        user_message = "💬 <b>Сообщение от администратора:</b>\n\n" f"{message.text}"

        await bot.send_message(
            chat_id=target_user_id, text=user_message, parse_mode="HTML"
        )

        # Подтверждаем админу и восстанавливаем кнопки запроса
        await message.answer(
            f"✅ <b>Сообщение отправлено</b>\n\n"
            f"👤 Пользователь: {request['first_name']} (@{request['username']})\n\n"
            f"Запрос остается активным. Выберите действие:",
            reply_markup=get_admin_request_keyboard(request_id),
            parse_mode="HTML",
        )

        logger.info(f"Admin sent message to user {target_user_id}")

    except Exception as e:
        logger.error(f"Failed to send message to user {target_user_id}: {e}")
        await message.answer("❌ Ошибка отправки сообщения")
    finally:
        await bot.session.close()
        await state.clear()


# ===== Отмена действий =====


@admin_router.callback_query(F.data.startswith("cancel_request_"))
async def callback_cancel_request(callback: CallbackQuery):
    """Отмена обработки запроса и возврат к исходному сообщению"""
    if not is_admin(callback.from_user.id):
        return

    request_id = callback.data.replace("cancel_request_", "")

    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    # Восстанавливаем исходное сообщение запроса
    admin_text = "🔑 <b>Новый запрос на ключ</b>\n\n" f"👤 Имя: {request['first_name']}"

    if request.get("last_name"):
        admin_text += f" {request['last_name']}"

    admin_text += (
        f"\n🆔 Telegram ID: <code>{request['tg_id']}</code>\n"
        f"👤 Username: @{request['username']}\n\n"
        f"🆔 Request ID: <code>{request_id}</code>"
    )

    await callback.message.edit_text(
        admin_text,
        reply_markup=get_admin_request_keyboard(request_id),
        parse_mode="HTML",
    )

    await callback.answer("↩️ Отменено")


@admin_router.callback_query(F.data.startswith("cancel_assign_"))
async def callback_cancel_assign(callback: CallbackQuery):
    """Отмена присвоения и возврат к запросу"""
    await callback_cancel_request(callback)


@admin_router.callback_query(F.data.startswith("back_to_request_"))
async def callback_back_to_request(callback: CallbackQuery):
    """Возврат к деталям запроса из выбора клиента"""
    if not is_admin(callback.from_user.id):
        return

    request_id = callback.data.replace("back_to_request_", "")
    request = db.get_pending_request(request_id)

    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    # Показываем детали запроса снова
    created_time = datetime.fromtimestamp(request["created_at"]).strftime(
        "%d.%m.%Y %H:%M"
    )
    await callback.message.edit_text(
        f"📨 <b>Новый запрос на ключ</b>\n\n"
        f"👤 Пользователь: {request['first_name']} "
        f"(@{request['username']})\n"
        f"🆔 Telegram ID: <code>{request['tg_id']}</code>\n"
        f"📅 Время запроса: {created_time}\n\n"
        f"Выберите действие:",
        reply_markup=get_admin_request_keyboard(request_id),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("template_"))
async def callback_template_inbound(callback: CallbackQuery, state: FSMContext):
    """
    Обработка выбора шаблона inbound - клонирование настроек и создание нового inbound
    """
    if not is_admin(callback.from_user.id):
        return

    # Парсим: template_{request_id}_{template_inbound_id}
    parts = callback.data.split("_")
    request_id = parts[1]
    template_id = int(parts[2])

    # Получаем запрос
    request = db.get_pending_request(request_id)
    if not request:
        await callback.answer("❌ Запрос не найден", show_alert=True)
        return

    # Получаем шаблон inbound
    template = xui_api.get_inbound(template_id)
    if not template:
        await callback.answer("❌ Шаблон не найден", show_alert=True)
        return

    tg_id = request["tg_id"]
    username = request["username"]

    # Генерируем email для клиента
    email = f"tg_{tg_id}_{username}"

    # Создаем новый inbound путем клонирования шаблона
    await callback.message.edit_text(
        f"⏳ Клонирую inbound '{template.remark}' и создаю ключ...", parse_mode="HTML"
    )

    new_inbound = xui_api.create_inbound_from_template(
        template_id=template_id, new_remark=f"User_{tg_id}_{username}"
    )

    if not new_inbound:
        await callback.answer("❌ Ошибка клонирования inbound", show_alert=True)
        await callback.message.edit_text(
            "❌ <b>Ошибка создания inbound</b>\n\n"
            "Проверьте логи 3x-ui или попробуйте другой шаблон.",
            parse_mode="HTML",
            reply_markup=get_admin_request_keyboard(request_id),
        )
        return

    new_inbound_id = new_inbound["id"]

    # Создаем клиента в новом inbound
    client = xui_api.create_client(
        inbound_id=new_inbound_id,
        email=email,
        total_gb=0,  # Безлимит
        expiry_time=0,  # Бессрочно
        enable=True,
    )

    if not client:
        await callback.answer("❌ Ошибка создания ключа", show_alert=True)
        return

    # Сохраняем в базу данных
    db.add_user_key(
        tg_id=tg_id,
        client_email=email,
        inbound_id=new_inbound_id,
        comment=f"Новый inbound {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    )

    # Получаем subscription URL
    sub_url = xui_api.get_subscription_url(email)

    # Отправляем ключ пользователю
    from aiogram import Bot

    bot = Bot(token=config.bot_token)

    try:
        user_message = (
            "✅ <b>Ваш ключ готов!</b>\n\n"
            f"🔑 Email: <code>{email}</code>\n"
            f"🖥 Inbound: {new_inbound['remark']}\n\n"
            f"🔗 <b>Subscription URL:</b>\n"
            f"<code>{sub_url}</code>\n\n"
            f"📱 <b>Как подключить:</b>\n"
            f"1. Установите v2rayNG (Android) или v2rayN (Windows)\n"
            f"2. Меню → Подписки → +\n"
            f"3. Вставьте URL выше\n"
            f"4. Обновите подписки\n\n"
            f"✅ Готово! Можете подключаться."
        )

        await bot.send_message(chat_id=tg_id, text=user_message, parse_mode="HTML")

        # Подтверждаем админу
        await callback.message.edit_text(
            f"✅ <b>Новый inbound создан и ключ выдан</b>\n\n"
            f"👤 Пользователь: {request['first_name']} (@{username})\n"
            f"🔑 Email: <code>{email}</code>\n"
            f"🖥 Inbound: {new_inbound['remark']}\n"
            f"🆔 Inbound ID: {new_inbound_id}\n\n"
            f"Пользователь получил уведомление с ключом.",
            parse_mode="HTML",
        )

        # Удаляем обработанный запрос
        db.delete_pending_request(request_id)

        await callback.answer("✅ Inbound создан, ключ выдан")
        logger.info(
            f"Admin created inbound {new_inbound_id} and key {email} for user {tg_id}"
        )

    except Exception as e:
        logger.error(f"Failed to send key to user {tg_id}: {e}")
        await callback.answer(
            "⚠️ Ключ создан, но не удалось отправить пользователю", show_alert=True
        )
    finally:
        await bot.session.close()


# ===== Управление блокировками =====


@admin_router.message(Command("bans"))
async def cmd_bans(message: Message):
    """Показать список заблокированных пользователей (только для админа)"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора")
        return

    blocked_users = db.get_all_blocked_users()

    if not blocked_users:
        await message.answer(
            "✅ <b>Список блокировок пуст</b>\n\n" "Нет заблокированных пользователей.",
            parse_mode="HTML",
        )
        return

    text = "🚫 <b>Заблокированные пользователи:</b>\n\n"

    for user in blocked_users:
        tg_id = user["tg_id"]
        username = user["username"] or "no_username"
        first_name = user["first_name"] or "User"
        blocked_until = datetime.fromtimestamp(user["blocked_until"])
        time_left = blocked_until - datetime.now()

        hours_left = int(time_left.total_seconds() / 3600)
        minutes_left = int((time_left.total_seconds() % 3600) / 60)

        text += (
            f"👤 {first_name} (@{username})\n"
            f"🆔 ID: <code>{tg_id}</code>\n"
            f"⏰ До: {blocked_until.strftime('%d.%m.%Y %H:%M')}\n"
            f"⏳ Осталось: {hours_left}ч {minutes_left}м\n"
            f"🔓 Разблокировать: /unban_{tg_id}\n\n"
        )

    await message.answer(text, parse_mode="HTML")
    logger.info(f"Admin {message.from_user.id} viewed blocked users list")


@admin_router.message(Command("requests"))
async def cmd_requests(message: Message):
    """Показать список всех незавершенных запросов с кнопками действий (только для админа)"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора")
        return

    pending_requests = db.get_all_pending_requests()

    if not pending_requests:
        await message.answer(
            "✅ <b>Нет незавершенных запросов</b>\n\n" "Все запросы обработаны.",
            parse_mode="HTML",
        )
        return

    # Отправляем заголовок
    await message.answer(
        f"📨 <b>Незавершенные запросы на ключи: {len(pending_requests)}</b>",
        parse_mode="HTML",
    )

    # Отправляем каждый запрос как отдельное сообщение с кнопками действий
    for request in pending_requests:
        tg_id = request["tg_id"]
        username = request["username"] or "no_username"
        first_name = request["first_name"] or "User"
        last_name = request["last_name"] or ""
        created_at = datetime.fromtimestamp(request["created_at"])
        request_id = request["request_id"]

        request_text = (
            f"📨 <b>Новый запрос на ключ</b>\n\n" f"👤 Пользователь: {first_name}"
        )

        if last_name:
            request_text += f" {last_name}"

        request_text += (
            f"\n🆔 Telegram ID: <code>{tg_id}</code>\n"
            f"👤 Username: @{username}\n"
            f"📅 Время запроса: {created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Выберите действие:"
        )

        await message.answer(
            request_text,
            reply_markup=get_admin_request_keyboard(request_id),
            parse_mode="HTML",
        )

    logger.info(
        f"Admin {message.from_user.id} viewed {len(pending_requests)} pending requests"
    )


@admin_router.message(Command("keys"))
async def cmd_keys(message: Message):
    """Показать список всех ключей с присвоенными пользователями (только для админа)"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора")
        return

    # Получаем все ключи из базы данных
    all_keys = db.get_all_keys_with_users()

    if not all_keys:
        await message.answer(
            "✅ <b>Нет присвоенных ключей</b>\n\n"
            "Ключи появятся после присвоения пользователям.",
            parse_mode="HTML",
        )
        return

    # Группируем ключи по email
    keys_dict = {}
    for key in all_keys:
        email = key["client_email"]
        if email not in keys_dict:
            keys_dict[email] = {
                "inbound_id": key["inbound_id"],
                "comment": key["comment"],
                "users": [],
            }
        keys_dict[email]["users"].append(
            {
                "tg_id": key["tg_id"],
                "username": key["username"],
                "first_name": key["first_name"],
            }
        )

    # Отправляем заголовок
    await message.answer(
        f"🔑 <b>Все ключи с привязками: {len(keys_dict)}</b>", parse_mode="HTML"
    )

    # Отправляем каждый ключ с пользователями
    for email, data in keys_dict.items():
        key_text = f"🔑 <b>{email}</b>\n\n"
        key_text += f"📝 Комментарий: {data['comment']}\n"
        key_text += f"👥 Пользователей: {len(data['users'])}\n\n"

        for idx, user in enumerate(data["users"], 1):
            username = user["username"] or "no_username"
            key_text += (
                f"{idx}. {user['first_name']} (@{username})\n"
                f"   🆔 <code>{user['tg_id']}</code>\n"
            )

        # Добавляем кнопки действий
        from bot.utils.keyboards import get_key_management_keyboard

        await message.answer(
            key_text,
            reply_markup=get_key_management_keyboard(email),
            parse_mode="HTML",
        )

    logger.info(f"Admin {message.from_user.id} viewed {len(keys_dict)} keys")


@admin_router.callback_query(F.data.startswith("manage_users_"))
async def callback_manage_users(callback: CallbackQuery):
    """Показать пользователей для конкретного ключа с опциями управления"""
    if not is_admin(callback.from_user.id):
        return

    email = callback.data.replace("manage_users_", "")

    # Получаем всех пользователей с этим ключом
    all_keys = db.get_all_keys_with_users()
    users_with_key = [k for k in all_keys if k["client_email"] == email]

    if not users_with_key:
        await callback.answer("❌ Нет пользователей с этим ключом", show_alert=True)
        return

    # Строим список пользователей с кнопками действий
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    buttons = []
    for user in users_with_key:
        tg_id = user["tg_id"]
        username = user["username"] or "no_username"
        first_name = user["first_name"] or "User"

        # Строка с информацией о пользователе и действиями
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {first_name} (@{username})",
                    callback_data="noop",  # Только для отображения
                )
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🗑 Отвязать", callback_data=f"unbind_{tg_id}_{email}"
                ),
                InlineKeyboardButton(
                    text="🚫 Забанить", callback_data=f"ban_user_{tg_id}"
                ),
            ]
        )

    # Кнопка "Назад"
    buttons.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keys_list")]
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        f"👥 <b>Пользователи ключа {email}</b>\n\n"
        f"Всего: {len(users_with_key)}\n\n"
        f"Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    await callback.answer()


@admin_router.callback_query(F.data.startswith("unbind_"))
async def callback_unbind_user(callback: CallbackQuery):
    """Отвязать ключ от пользователя"""
    if not is_admin(callback.from_user.id):
        return

    # Парсим: unbind_{tg_id}_{email}
    parts = callback.data.replace("unbind_", "").rsplit("_", 1)
    if len(parts) != 2:
        await callback.answer("❌ Неверный формат", show_alert=True)
        return

    tg_id = int(parts[0])
    email = parts[1]

    # Удаляем привязку
    db.remove_user_key(tg_id, email)

    await callback.answer(f"✅ Ключ {email} отвязан от пользователя {tg_id}")

    # Обновляем список
    await callback_manage_users(callback)

    logger.info(f"Admin {callback.from_user.id} unbound {email} from user {tg_id}")


@admin_router.callback_query(F.data.startswith("ban_user_"))
async def callback_ban_user(callback: CallbackQuery):
    """Забанить пользователя из управления ключами"""
    if not is_admin(callback.from_user.id):
        return

    tg_id = int(callback.data.replace("ban_user_", ""))

    # Блокируем пользователя на 24 часа
    db.block_user(tg_id, hours=24)

    # Получаем информацию о пользователе
    user = db.get_user(tg_id)
    username = user["username"] if user else "unknown"

    await callback.answer(f"🚫 Пользователь {username} заблокирован на 24ч")

    logger.info(f"Admin {callback.from_user.id} banned user {tg_id}")


@admin_router.callback_query(F.data == "admin_keys_list")
async def callback_admin_keys_list(callback: CallbackQuery):
    """Возврат к списку ключей"""
    if not is_admin(callback.from_user.id):
        return

    await callback.message.delete()
    await callback.answer("Используйте /keys для просмотра списка")


@admin_router.callback_query(F.data == "noop")
async def callback_noop(callback: CallbackQuery):
    """Callback без операции"""
    await callback.answer()


@admin_router.message(Command(commands=["unban"], magic=F.args.regexp(r"^\d+$")))
async def cmd_unban(message: Message):
    """Разблокировать пользователя по ID (только для админа)"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора")
        return

    # Извлекаем ID пользователя из команды
    tg_id = (
        int(message.text.split("_")[1])
        if "_" in message.text
        else int(message.text.split()[1])
    )

    # Проверяем, существует ли пользователь
    user = db.get_user(tg_id)
    if not user:
        await message.answer(
            f"❌ Пользователь с ID {tg_id} не найден в базе данных", parse_mode="HTML"
        )
        return

    # Проверяем, заблокирован ли пользователь
    if not db.is_user_blocked(tg_id):
        await message.answer(
            f"ℹ️ Пользователь {user['first_name']} (@{user['username']}) не заблокирован",
            parse_mode="HTML",
        )
        return

    # Разблокируем пользователя
    db.unblock_user(tg_id)

    await message.answer(
        f"✅ <b>Пользователь разблокирован</b>\n\n"
        f"👤 {user['first_name']} (@{user['username']})\n"
        f"🆔 ID: <code>{tg_id}</code>\n\n"
        f"Пользователь может снова использовать бота.",
        parse_mode="HTML",
    )

    logger.info(f"Admin {message.from_user.id} unblocked user {tg_id}")


@admin_router.message(F.text.regexp(r"^/unban_\d+$"))
async def cmd_unban_inline(message: Message):
    """Обработка inline команды разблокировки /unban_123456"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администратора")
        return

    # Извлекаем ID пользователя
    tg_id = int(message.text.split("_")[1])

    # Проверяем, существует ли пользователь
    user = db.get_user(tg_id)
    if not user:
        await message.answer(
            f"❌ Пользователь с ID {tg_id} не найден", parse_mode="HTML"
        )
        return

    # Проверяем, заблокирован ли пользователь
    if not db.is_user_blocked(tg_id):
        await message.answer(
            f"ℹ️ Пользователь {user['first_name']} не заблокирован", parse_mode="HTML"
        )
        return

    # Разблокируем пользователя
    db.unblock_user(tg_id)

    await message.answer(
        f"✅ <b>Пользователь разблокирован</b>\n\n"
        f"👤 {user['first_name']} (@{user['username']})\n"
        f"🆔 ID: <code>{tg_id}</code>",
        parse_mode="HTML",
    )

    logger.info(f"Admin {message.from_user.id} unblocked user {tg_id}")


# ===== Ответ на сообщения пользователей =====


@admin_router.message(F.text & F.reply_to_message & F.from_user.id == config.admin_id)
async def handle_admin_reply(message: Message):
    """
    Обработка reply от админа на сообщения пользователей
    Отправляет ответ пользователю
    """

    # Извлекаем ID пользователя из оригинального сообщения
    original_text = message.reply_to_message.text

    if not original_text:
        logger.warning("Reply message has no text")
        return

    logger.debug(f"Admin reply received. Original text: {original_text[:200]}")

    # Ищем ID в разных форматах
    import re

    # Пробуем разные варианты regex (более гибкие)
    patterns = [
        r"🆔\s*(?:ID|Telegram ID):\s*<code>(\d+)</code>",  # С тегом code
        r"🆔\s*(?:ID|Telegram ID):\s*(\d+)",  # Без тега code
        r"(?:ID|Telegram ID):\s*<code>(\d+)</code>",  # Без эмодзи с code
        r"(?:ID|Telegram ID):\s*(\d+)",  # Без эмодзи без code
        r"👤.*?(\d{9,})",  # Любое число 9+ цифр (Telegram ID)
    ]

    match = None
    for pattern in patterns:
        match = re.search(pattern, original_text, re.DOTALL | re.IGNORECASE)
        if match:
            logger.debug(f"ID found with pattern: {pattern}")
            break

    if not match:
        logger.warning(f"Could not extract user ID from message: {original_text[:200]}")
        await message.answer(
            "❌ Не удалось определить ID пользователя из сообщения.\n\n"
            "Убедитесь, что отвечаете на сообщение от пользователя или запрос."
        )
        return

    user_id = int(match.group(1))
    logger.info(f"Extracted user_id: {user_id}")

    # Проверяем, существует ли пользователь
    user = db.get_user(user_id)
    if not user:
        await message.answer(f"❌ Пользователь с ID {user_id} не найден в базе данных.")
        return

    # Отправляем ответ пользователю
    from aiogram import Bot

    bot = Bot(token=config.bot_token)

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(f"💬 <b>Сообщение от администратора:</b>\n\n" f"{message.text}"),
            parse_mode="HTML",
        )

        # Подтверждаем админу
        username_display = user["username"] or "нет username"
        await message.answer(
            f"✅ <b>Сообщение отправлено</b>\n\n"
            f"👤 Получатель: {user['first_name']} (@{username_display})\n"
            f"🆔 ID: <code>{user_id}</code>",
            parse_mode="HTML",
        )

        logger.info(f"Admin reply sent to user {user_id}")

    except Exception as e:
        logger.error(f"Failed to send admin reply to user {user_id}: {e}")
        await message.answer(
            f"❌ Ошибка отправки сообщения пользователю {user_id}.\n\n"
            f"Возможно, пользователь заблокировал бота."
        )
    finally:
        await bot.session.close()
