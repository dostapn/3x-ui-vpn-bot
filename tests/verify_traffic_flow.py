"""
Спек: проверка потока отчётов по трафику (all_time).
Тестирует get_all_time_report_data, all_time_snapshots, traffic_history, get_period_report_data.
Использует копию БД — не изменяет боевые данные.
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.database import db  # noqa: E402


def _run() -> bool:
    """Возвращает True при успехе, False при ошибке."""
    test_db_path = "/app/data/x-ui-test.db"
    if not os.path.exists("/app/data/x-ui.db"):
        print("SKIP: /app/data/x-ui.db not found (run in Docker)")
        return True

    shutil.copy("/app/data/x-ui.db", test_db_path)
    db.db_path = test_db_path

    test_email = "test_flow_user"
    test_inbound_id = 999
    all_time_val = 1073741824  # 1 GB

    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO inbounds "
                "(id, remark, port, protocol, settings, stream_settings, tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (test_inbound_id, "test_inbound", 12345, "vless", "{}", "{}", "test"),
            )
            cursor.execute(
                "INSERT OR REPLACE INTO client_traffics "
                "(id, inbound_id, enable, email, up, down, expiry_time, total, all_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (9999, test_inbound_id, 1, test_email, 0, 0, 0, 0, all_time_val),
            )
            conn.commit()

        report_date, prev_date = "2026-02-01", "2026-01-31"
        db.save_all_time_snapshot(test_email, all_time_val - 524288000, prev_date)

        rows = db.get_all_time_report_data(report_date, prev_date)
        if not any(r["email"] == test_email and r.get("delta") == 524288000 for r in rows):
            print("ERROR: get_all_time_report_data failed")
            return False

        db.save_traffic_snapshot(test_email, 0, 524288000, report_date)
        period_rows = db.get_period_report_data(prev_date, report_date, 2)
        if not any(
            r["email"] == test_email and r.get("period_traffic") == 524288000
            for r in period_rows
        ):
            print("ERROR: get_period_report_data failed")
            return False

        print("SUCCESS: all checks passed")
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        raise
    finally:
        if os.path.exists(test_db_path):
            os.remove(test_db_path)


if __name__ == "__main__":
    ok = _run()
    sys.exit(0 if ok else 1)
