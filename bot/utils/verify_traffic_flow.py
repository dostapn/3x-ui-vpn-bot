import sys
import os
import logging

# Добавляем корневой каталог проекта в путь Python
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Импорт после настройки путей
from bot.database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_flow():
    import shutil

    # Используем отдельную тестовую БД вместо боевой
    test_db_path = "/app/data/x-ui-test.db"
    shutil.copy("/app/data/x-ui.db", test_db_path)

    # Переопределяем путь к БД в объекте db
    db.db_path = test_db_path

    test_email = "test_flow_user"
    test_inbound_id = 999
    # 500 MB up, 1 GB down
    up_val = 524288000
    down_val = 1073741824

    print(f"Testing with TEST DB copy: {db.db_path}")

    # 1. Устанавливаем тестовые данные в БД
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()

            print("\n--- 1. Setting up test data in client_traffics ---")
            # Убеждаемся, что inbound существует
            cursor.execute(
                "INSERT OR IGNORE INTO inbounds (id, remark, port, protocol, settings, stream_settings, tag) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (test_inbound_id, "test_inbound", 12345, "vless", "{}", "{}", "test"),
            )

            # Записываем трафик клиента
            cursor.execute(
                "INSERT OR REPLACE INTO client_traffics (id, inbound_id, enable, email, up, down, expiry_time, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (9999, test_inbound_id, 1, test_email, up_val, down_val, 0, 0),
            )

            print(f"Set traffic for {test_email}: UP={up_val}, DOWN={down_val}")
            conn.commit()

    except Exception as e:
        print(f"ERROR during test setup: {e}")
        return

    # 2. Проверяем get_xui_traffic_stats
    print("\n--- 2. Verifying get_xui_traffic_stats ---")
    stats = db.get_xui_traffic_stats()

    # Проверяем, есть ли трафик клиента в статистике
    found_stats = False
    for row in stats:
        # Проверяем, есть ли трафик клиента в статистике
        if row["email"] == test_email:
            # Выводим статистику трафика клиента
            print(f"Found in stats: {row['email']} UP={row['up']} DOWN={row['down']}")
            # Проверяем, соответствует ли трафик клиента тестовым данным
            if row["up"] == up_val and row["down"] == down_val:
                found_stats = True
                print("SUCCESS: Stats values match!")
            break

    if not found_stats:
        print("ERROR: Stats not found or incorrect!")
        return

    # 3. Моделируем ежедневную задачу (сохранение снимка)
    print("\n--- 3. Simulating Daily Job (Save Snapshot) ---")
    report_date = "2026-02-01"  # Фиксированная дата для теста
    db.save_traffic_snapshot(test_email, up_val, down_val, report_date)
    print(f"Saved snapshot for date: {report_date}")

    # 4. Проверяем историю трафика
    print("\n--- 4. Verifying Traffic History ---")
    history = db.get_traffic_stats(report_date, report_date)

    # Проверяем, есть ли трафик клиента в истории
    found_hist = False
    for row in history:
        # Проверяем, есть ли трафик клиента в истории
        if row["email"] == test_email:
            # Выводим статистику трафика клиента в истории
            print(f"Found in history: {dict(row)}")
            # Проверяем, соответствует ли трафик клиента тестовым данным
            if row["total_up"] == up_val and row["total_down"] == down_val:
                found_hist = True
                print("SUCCESS: History values match!")
            break

    if not found_hist:
        print("ERROR: History record not found or incorrect!")
        return

    # 5. Сбрасываем счетчики трафика
    print("\n--- 5. Simulating Reset ---")
    db.reset_xui_traffic()

    # Проверяем, сбросились ли счетчики трафика
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT up, down FROM client_traffics WHERE email = ?", (test_email,))
        row = cursor.fetchone()
        if row:
            print(f"After reset: UP={row['up']}, DOWN={row['down']}")
            if row["up"] == 0 and row["down"] == 0:
                print("SUCCESS: Counters reset to 0.")
            else:
                print("ERROR: Counters not reset!")

    # Очищаем тестовую БД
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        print(f"\nTest DB removed: {test_db_path}")


if __name__ == "__main__":
    test_flow()
