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

            # Таблица истории трафика
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS traffic_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    up INTEGER DEFAULT 0,
                    down INTEGER DEFAULT 0,
                    date TEXT NOT NULL,
                    timestamp INTEGER DEFAULT (strftime('%s', 'now')),
                    UNIQUE(email, date)
                )
            """
            )

            # Снимки all_time для расчёта дневного трафика (когда up/down не обновляются)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS all_time_snapshots (
                    email TEXT NOT NULL,
                    all_time INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    PRIMARY KEY (email, date)
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
            cursor.execute("SELECT blocked_until FROM telegram_users WHERE tg_id = ?", (tg_id,))
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
            logger.info(f"Blocked user {tg_id} until {datetime.fromtimestamp(blocked_until)}")

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

    def get_user_keys_by_inbound(self, tg_id: int, inbound_id: int) -> List[Dict[str, Any]]:
        """Получить все ключи пользователя в конкретном inbound"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT client_email FROM user_keys
                WHERE tg_id = ? AND inbound_id = ?
            """,
                (tg_id, inbound_id),
            )
            return [dict(row) for row in cursor.fetchall()]

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
            cursor.execute("SELECT * FROM pending_requests WHERE request_id = ?", (request_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_pending_request(self, request_id: str):
        """Удалить незавершённый запрос"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pending_requests WHERE request_id = ?", (request_id,))
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
            cursor.execute("UPDATE telegram_users SET blocked_until = 0 WHERE tg_id = ?", (tg_id,))
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

    # ===== История трафика =====

    def save_traffic_snapshot(self, email: str, up: int, down: int, date: str):
        """Сохранить снимок трафика за день"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO traffic_history (email, up, down, date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(email, date) DO UPDATE SET
                    up = excluded.up,
                    down = excluded.down
            """,
                (email, up, down, date),
            )

    def get_traffic_stats(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Получить суммарный трафик за период (только клиенты с записями в traffic_history)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    email,
                    SUM(up) as total_up,
                    SUM(down) as total_down
                FROM traffic_history
                WHERE date BETWEEN ? AND ?
                GROUP BY email
                ORDER BY (SUM(up) + SUM(down)) DESC
            """,
                (start_date, end_date),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_period_report_data(
        self, start_date: str, end_date: str, period_days: int
    ) -> List[Dict[str, Any]]:
        """
        Данные для отчёта за период: ВСЕ клиенты с all_time, трафик за период, активность.
        period_days — количество дней в периоде (7 или 30).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        c.email,
                        c.all_time,
                        COALESCE(th.period_traffic, 0) as period_traffic,
                        COALESCE(th.active_days, 0) as active_days
                    FROM client_traffics c
                    LEFT JOIN (
                        SELECT
                            email,
                            SUM(up + down) as period_traffic,
                            SUM(CASE WHEN (up + down) > 0 THEN 1 ELSE 0 END) as active_days
                        FROM traffic_history
                        WHERE date BETWEEN ? AND ?
                        GROUP BY email
                    ) th ON c.email = th.email
                    ORDER BY c.all_time DESC
                """,
                    (start_date, end_date),
                )
            except sqlite3.OperationalError:
                logger.warning("Could not get period report data")
                return []

            return [
                {
                    "email": row["email"],
                    "all_time": int(row["all_time"]),
                    "period_traffic": int(row["period_traffic"]),
                    "active_days": int(row["active_days"]),
                    "period_days": period_days,
                }
                for row in cursor.fetchall()
            ]

    def get_xui_traffic_stats(self) -> List[Dict[str, Any]]:
        """Получить текущую статистику трафика из таблиц X-UI"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Проверяем наличие таблицы client_traffics
            try:
                cursor.execute("SELECT 1 FROM client_traffics LIMIT 1")
            except sqlite3.OperationalError:
                # Таблицы нет, возможно это не БД X-UI
                logger.warning("Table client_traffics not found. Is DB_PATH correct?")
                return []

            cursor.execute(
                """
                SELECT
                    c.email,
                    c.up,
                    c.down,
                    i.remark as inbound_remark
                FROM client_traffics c
                JOIN inbounds i ON c.inbound_id = i.id
                WHERE c.up > 0 OR c.down > 0
                ORDER BY (c.up + c.down) DESC
            """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_time_snapshot(self, email: str, date: str) -> Optional[int]:
        """Получить сохранённый all_time для клиента на дату."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT all_time FROM all_time_snapshots WHERE email = ? AND date = ?",
                (email, date),
            )
            row = cursor.fetchone()
            return row["all_time"] if row else None

    def save_all_time_snapshot(self, email: str, all_time: int, date: str):
        """Сохранить снимок all_time для расчёта дневного трафика."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO all_time_snapshots (email, all_time, date)
                VALUES (?, ?, ?)
                ON CONFLICT(email, date) DO UPDATE SET all_time = excluded.all_time
            """,
                (email, all_time, date),
            )

    def backfill_daily_report(
        self, report_date: str, report_rows: List[Dict[str, Any]]
    ) -> None:
        """
        Сохраняет all_time снимки и traffic_history после ежедневного отчёта.
        report_rows — результат get_all_time_report_data.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT email, all_time FROM client_traffics")
                for row in cursor.fetchall():
                    cursor.execute(
                        """
                        INSERT INTO all_time_snapshots (email, all_time, date)
                        VALUES (?, ?, ?)
                        ON CONFLICT(email, date) DO UPDATE SET all_time = excluded.all_time
                        """,
                        (row["email"], int(row["all_time"]), report_date),
                    )
            except Exception as e:
                logger.warning("Could not save all_time snapshots: %s", e)

            for row in report_rows:
                delta = row.get("delta")
                down = int(delta) if delta is not None and delta >= 0 else 0
                cursor.execute(
                    """
                    INSERT INTO traffic_history (email, up, down, date)
                    VALUES (?, ?, ?)
                    ON CONFLICT(email, date) DO UPDATE SET up = excluded.up, down = excluded.down
                    """,
                    (row["email"], 0, down, report_date),
                )

    def get_snapshots_for_email(self, email: str, max_days: int = 31) -> List[Dict[str, Any]]:
        """Получить снимки all_time для клиента за последние max_days дней (по убыванию даты)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT date, all_time FROM all_time_snapshots
                WHERE email = ?
                ORDER BY date DESC
                LIMIT ?
            """,
                (email, max_days),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_time_report_data(
        self, report_date: str, prev_date: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Данные для отчёта по all_time: клиенты с delta и activity.
        report_date — дата отчёта, prev_date — вчера для расчёта delta.
        limit — макс. число клиентов в отчёте (по умолчанию 500).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT c.email, c.all_time, i.remark as inbound_remark
                    FROM client_traffics c
                    JOIN inbounds i ON c.inbound_id = i.id
                    ORDER BY c.all_time DESC
                    LIMIT ?
                """,
                    (limit,),
                )
            except sqlite3.OperationalError:
                logger.warning("Table client_traffics not found")
                return []

            rows = [dict(r) for r in cursor.fetchall()]
            if not rows:
                return []

            cursor.execute(
                "SELECT email, all_time FROM all_time_snapshots WHERE date = ?",
                (prev_date,),
            )
            prev_snapshots = {r["email"]: r["all_time"] for r in cursor.fetchall()}

            # Один запрос: все снимки для emails отчёта (пачками по 100 email)
            emails = [r["email"] for r in rows]
            snapshots_by_email: Dict[str, List[Dict[str, Any]]] = {e: [] for e in emails}
            chunk_size = 100
            for i in range(0, len(emails), chunk_size):
                chunk = emails[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"""
                    SELECT email, date, all_time FROM all_time_snapshots
                    WHERE email IN ({placeholders})
                    ORDER BY email, date DESC
                """,
                    chunk,
                )
                for row in cursor.fetchall():
                    e = row["email"]
                    if len(snapshots_by_email[e]) < 31:
                        snapshots_by_email[e].append(dict(row))

            result = []
            for row in rows:
                email = row["email"]
                current = int(row["all_time"])
                prev = prev_snapshots.get(email)
                delta = (current - prev) if prev is not None else None

                snapshots = snapshots_by_email.get(email, [])
                consecutive_inactive = 0
                active_days = 0
                total_days = 0

                deltas: List[int] = []
                if prev is not None:
                    deltas.append(current - prev)
                for j in range(len(snapshots) - 1):
                    deltas.append(snapshots[j]["all_time"] - snapshots[j + 1]["all_time"])

                if deltas:
                    total_days = min(len(deltas), 30)
                    for d in deltas[:30]:
                        if d > 0:
                            active_days += 1
                    for d in deltas:
                        if d > 0:
                            break
                        consecutive_inactive += 1

                result.append(
                    {
                        "email": email,
                        "all_time": current,
                        "prev_all_time": prev,
                        "delta": delta,
                        "inbound_remark": row["inbound_remark"],
                        "consecutive_inactive": consecutive_inactive,
                        "active_days": active_days,
                        "total_days": total_days,
                        "has_prev": prev is not None,
                    }
                )
            return result

    def reset_xui_traffic(self):
        """Сбросить счетчики трафика в таблицах X-UI"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE client_traffics SET up=0, down=0")
                cursor.execute("UPDATE inbounds SET up=0, down=0")
                logger.info("X-UI traffic counters reset successfully")
            except sqlite3.OperationalError as e:
                logger.error(f"Failed to reset X-UI traffic: {e}")


# Глобальный экземпляр базы данных
db = Database(config.db_path)
