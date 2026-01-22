"""
Inline клавиатуры для Telegram бота
"""

from typing import List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from py3xui.inbound import Inbound


def get_main_keyboard(has_keys: bool = False) -> InlineKeyboardMarkup:
    """
    Главное меню для клиентов

    Args:
        has_keys: Есть ли у пользователя ключи

    Returns:
        InlineKeyboardMarkup с кнопками главного меню
    """
    buttons = [
        [InlineKeyboardButton(text="🔑 Мои ключи", callback_data="get_keys")],
        [
            InlineKeyboardButton(
                text="➕ Запросить новый ключ", callback_data="request_key"
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_request_keyboard(request_id: str) -> InlineKeyboardMarkup:
    """
    Админская клавиатура для обработки запросов на ключи

    Args:
        request_id: Уникальный идентификатор запроса

    Returns:
        InlineKeyboardMarkup с кнопками действий админа
    """
    buttons = [
        [
            InlineKeyboardButton(
                text="✅ Выдать новый ключ", callback_data=f"accept_{request_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔄 Присвоить существующий", callback_data=f"assign_{request_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отклонить", callback_data=f"reject_{request_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="⛔ Отклонить и заблокировать",
                callback_data=f"denied_{request_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="💬 Написать сообщение", callback_data=f"ask_{request_id}"
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_inbound_selection_keyboard(
    request_id: str,
    inbounds: List[Inbound],
    show_create_new: bool = True,
    prefix: str = "select_inbound",
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора inbound при создании нового ключа

    Args:
        request_id: Идентификатор запроса для отслеживания callback
        inbounds: Список доступных inbound'ов
        show_create_new: Показывать кнопку "Создать новый inbound"
        prefix: Префикс callback data (по умолчанию: "select_inbound", для присвоения: "assign_inbound")

    Returns:
        InlineKeyboardMarkup с кнопками выбора inbound
    """
    buttons = []

    # Добавляем кнопку для каждого существующего inbound
    for inbound in inbounds:
        status = "✅" if inbound.enable else "❌"
        button_text = f"{status} {inbound.remark} (:{inbound.port})"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"{prefix}_{request_id}_{inbound.id}",
                )
            ]
        )

    # Добавляем кнопку "Создать новый inbound"
    if show_create_new:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="➕ Создать новый inbound",
                    callback_data=f"create_inbound_{request_id}",
                )
            ]
        )

    # Добавляем кнопку отмены
    buttons.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена", callback_data=f"cancel_request_{request_id}"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_template_inbound_keyboard(
    request_id: str, inbounds: List[Inbound]
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора шаблона inbound для клонирования

    Args:
        request_id: Идентификатор запроса
        inbounds: Список inbound'ов для использования в качестве шаблонов

    Returns:
        InlineKeyboardMarkup с кнопками выбора шаблона
    """
    buttons = []

    for inbound in inbounds:
        button_text = f"📋 {inbound.remark} ({inbound.protocol})"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"template_{request_id}_{inbound.id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена", callback_data=f"cancel_request_{request_id}"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_client_list_keyboard(
    clients: List[dict], request_id: str, page: int = 0, per_page: int = 10
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора существующего клиента для присвоения

    Args:
        clients: Список словарей клиентов с 'email' и 'inbound_remark'
        request_id: Идентификатор запроса
        page: Номер текущей страницы
        per_page: Клиентов на странице

    Returns:
        InlineKeyboardMarkup с кнопками выбора клиента
    """
    buttons = []

    # Вычисляем пагинацию
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_clients = clients[start_idx:end_idx]

    # Добавляем кнопку для каждого клиента
    for client_info in page_clients:
        client = client_info["client"]

        # Импортируем database здесь, чтобы избежать циклических импортов
        from bot.database import db

        # Подсчитываем пользователей с этим email
        user_count = db.count_users_by_email(client.email)

        # Форматируем текст кнопки: email (comment) [N users]
        comment = (
            client.comment if hasattr(client, "comment") and client.comment else ""
        )

        if comment:
            button_text = f"{client.email} ({comment}) [{user_count}]"
        else:
            button_text = f"{client.email} [{user_count}]"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"assign_client_{request_id}_{client.email}",
                )
            ]
        )

    # Кнопки пагинации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Назад", callback_data=f"clients_page_{request_id}_{page-1}"
            )
        )
    if end_idx < len(clients):
        nav_buttons.append(
            InlineKeyboardButton(
                text="➡️ Вперед", callback_data=f"clients_page_{request_id}_{page+1}"
            )
        )

    if nav_buttons:
        buttons.append(nav_buttons)

    # Кнопка возврата к запросу
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к запросу", callback_data=f"back_to_request_{request_id}"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_key_actions_keyboard(client_email: str) -> InlineKeyboardMarkup:
    """
    Клавиатура действий для отдельного ключа

    Args:
        client_email: Идентификатор email клиента

    Returns:
        InlineKeyboardMarkup с кнопками действий для ключа
    """
    buttons = [
        [InlineKeyboardButton(text="📱 QR-код", callback_data=f"qr_{client_email}")],
        [
            InlineKeyboardButton(
                text="📊 Статистика", callback_data=f"stats_{client_email}"
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Простая кнопка возврата в главное меню"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")]
        ]
    )


def get_key_management_keyboard(client_email: str) -> InlineKeyboardMarkup:
    """
    Клавиатура управления ключом (только для админа)

    Args:
        client_email: Идентификатор email клиента

    Returns:
        InlineKeyboardMarkup с кнопками управления
    """
    buttons = [
        [
            InlineKeyboardButton(
                text="👥 Управление пользователями",
                callback_data=f"manage_users_{client_email}",
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
