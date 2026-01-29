"""
Операции с базой данных для VPN Bot
Управление telegram пользователями, запросами и связями пользователь-ключ
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from bot.config import config

logger = logging.getLogger(__name__)


class Database:
    """Менеджер базы данных для операций бота"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для подключений к БД"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _init_tables(self):
        """Инициализация таблиц бота, если они не существуют"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Таблица пользователей Telegram
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_users (
                    tg_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    blocked_until INTEGER DEFAULT 0,
                    created_at INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """
            )

            # Связь пользователь-ключ (один ключ → много пользователей)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    client_email TEXT NOT NULL,
                    inbound_id INTEGER NOT NULL,
                    comment TEXT,
                    created_at INTEGER DEFAULT (strftime('%s', 'now')),
                    FOREIGN KEY(tg_id) REFERENCES telegram_users(tg_id),
                    UNIQUE(tg_id, client_email)
                )
            """
            )

            # Таблица незавершённых запросов
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_requests (
                    request_id TEXT PRIMARY KEY,
                    tg_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at INTEGER DEFAULT (strftime('%s', 'now')),
                    FOREIGN KEY(tg_id) REFERENCES telegram_users(tg_id)
                )
            """
            )

            # Таблица настроек бота
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """
            )

            conn.commit()
            logger.info("Database tables initialized")

    # ===== Настройки бота =====

    def get_setting(self, key: str, default: Any = None) -> Optional[str]:
        """Получить значение настройки по ключу"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def update_setting(self, key: str, value: str):
        """Обновить или создать настройку"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bot_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
                (key, value),
            )
            logger.debug(f"Updated setting {key} = {value}")

    # ===== Пользователи Telegram =====

    def save_user(
        self,
        tg_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str] = None,
    ):
        """Сохранить или обновить пользователя telegram"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO telegram_users (tg_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name
            """,
                (tg_id, username, first_name, last_name),
            )
            logger.debug(f"Saved user {tg_id} (@{username})")

    def get_user(self, tg_id: int) -> Optional[Dict[str, Any]]:
        """Получить пользователя по telegram ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM telegram_users WHERE tg_id = ?", (tg_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def is_user_blocked(self, tg_id: int) -> bool:
        """Проверить, заблокирован ли пользователь"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT blocked_until FROM telegram_users WHERE tg_id = ?", (tg_id,)
            )
            row = cursor.fetchone()
            if row and row["blocked_until"]:
                return row["blocked_until"] > int(datetime.now().timestamp())
            return False

    def block_user(self, tg_id: int, hours: int = 24):
        """Заблокировать пользователя на указанное количество часов"""
        blocked_until = int((datetime.now() + timedelta(hours=hours)).timestamp())
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE telegram_users SET blocked_until = ? WHERE tg_id = ?",
                (blocked_until, tg_id),
            )
            logger.info(
                f"Blocked user {tg_id} until {datetime.fromtimestamp(blocked_until)}"
            )

    # ===== Ключи пользователей =====

    def add_user_key(
        self,
        tg_id: int,
        client_email: str,
        inbound_id: int,
        comment: Optional[str] = None,
    ):
        """Привязать ключ к пользователю"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO user_keys (tg_id, client_email, inbound_id, comment)
                VALUES (?, ?, ?, ?)
            """,
                (tg_id, client_email, inbound_id, comment),
            )
            logger.info(f"Added key {client_email} to user {tg_id}")

    def get_user_keys(self, tg_id: int) -> List[Dict[str, Any]]:
        """Получить все ключи пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT uk.*, ct.up, ct.down, ct.total, ct.expiry_time, ct.enable
                FROM user_keys uk
                LEFT JOIN client_traffics ct ON uk.client_email = ct.email
                WHERE uk.tg_id = ?
                ORDER BY uk.created_at DESC
            """,
                (tg_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def remove_user_key(self, tg_id: int, client_email: str):
        """Удалить привязку ключа от пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM user_keys WHERE tg_id = ? AND client_email = ?",
                (tg_id, client_email),
            )
            logger.info(f"Removed key {client_email} from user {tg_id}")

    def count_users_by_email(self, client_email: str) -> int:
        """Подсчитать количество пользователей с этим ключом"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(DISTINCT tg_id) FROM user_keys WHERE client_email = ?",
                (client_email,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_all_keys_with_users(self) -> List[Dict[str, Any]]:
        """Получить все ключи с привязанными пользователями"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    uk.client_email,
                    uk.inbound_id,
                    uk.comment,
                    uk.tg_id,
                    tu.username,
                    tu.first_name
                FROM user_keys uk
                LEFT JOIN telegram_users tu ON uk.tg_id = tu.tg_id
                ORDER BY uk.client_email, tu.first_name
            """
            )
            return [dict(row) for row in cursor.fetchall()]

    # ===== Незавершённые запросы =====

    def create_pending_request(
        self,
        request_id: str,
        tg_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str] = None,
    ):
        """Создать незавершённый запрос на ключ"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO pending_requests (request_id, tg_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?, ?)
            """,
                (request_id, tg_id, username, first_name, last_name),
            )
            logger.debug(f"Created pending request {request_id} for user {tg_id}")

    def get_pending_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Получить незавершённый запрос по ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM pending_requests WHERE request_id = ?", (request_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_pending_request(self, request_id: str):
        """Удалить незавершённый запрос"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM pending_requests WHERE request_id = ?", (request_id,)
            )
            logger.debug(f"Deleted pending request {request_id}")

    def get_pending_requests_by_user(self, tg_id: int) -> List[Dict[str, Any]]:
        """Получить все незавершённые запросы конкретного пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pending_requests WHERE tg_id = ?", (tg_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_all_pending_requests(self) -> List[Dict[str, Any]]:
        """Получить все незавершённые запросы, отсортированные по дате"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pending_requests ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_all_blocked_users(self) -> List[Dict[str, Any]]:
        """Получить всех заблокированных пользователей"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            current_time = int(datetime.now().timestamp())
            cursor.execute(
                """
                SELECT tg_id, username, first_name, last_name, blocked_until
                FROM telegram_users
                WHERE blocked_until > ?
                ORDER BY blocked_until DESC
                """,
                (current_time,),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def unblock_user(self, tg_id: int):
        """Разблокировать пользователя (установить blocked_until = 0)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE telegram_users SET blocked_until = 0 WHERE tg_id = ?", (tg_id,)
            )
            logger.info(f"User {tg_id} has been unblocked")

    # ===== Статистика =====

    def get_user_count(self) -> int:
        """Получить общее количество зарегистрированных пользователей"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM telegram_users")
            return cursor.fetchone()["count"]

    def get_active_keys_count(self) -> int:
        """Получить общее количество активных привязок ключей"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM user_keys")
            return cursor.fetchone()["count"]


# Глобальный экземпляр базы данных
db = Database(config.db_path)
