import argparse
import sys

from werkzeug.security import generate_password_hash

from database import get_connection, init_db


def reset_admin(username: str, password: str) -> None:
    username = (username or "").strip()
    if not username:
        raise SystemExit("username is required")
    if not password or len(password) < 8:
        raise SystemExit("password must be at least 8 characters")

    init_db()
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT id, username
            FROM users
            WHERE username='admin' OR is_admin=1
            ORDER BY CASE WHEN username='admin' THEN 0 ELSE 1 END, id
            LIMIT 1
            """
        )
        source = cur.fetchone()
        if not source:
            raise SystemExit("existing admin account was not found")

        cur.execute("SELECT id FROM users WHERE username=?", (username,))
        target = cur.fetchone()
        if target and target["id"] != source["id"]:
            raise SystemExit("requested username already belongs to another account")

        cur.execute("UPDATE users SET is_admin=0 WHERE is_admin=1")
        cur.execute(
            """
            UPDATE users
               SET username=?,
                   password_hash=?,
                   is_admin=1,
                   is_active=1,
                   status='active',
                   subscription_end='2099-12-31 23:59:59',
                   active_session_id=NULL
             WHERE id=?
            """,
            (username, generate_password_hash(password), source["id"]),
        )
        if cur.rowcount != 1:
            raise SystemExit("admin update did not affect exactly one account")

        conn.commit()

        row = cur.execute(
            "SELECT username, is_admin, is_active, status, telegram_chat_id "
            "FROM users WHERE id=?",
            (source["id"],),
        ).fetchone()

        assert row
        assert row["username"] == username
        assert int(row["is_admin"] or 0) == 1
        assert int(row["is_active"] or 0) == 1
        assert row["status"] == "active"

        print("ADMIN_RESET=PASS")
        print("ADMIN_USERNAME=" + row["username"])
        print("ADMIN_ROLE=PASS")
        print("ADMIN_ACTIVE=PASS")
        print("ADMIN_TELEGRAM_LINK=" + ("PRESENT" if row["telegram_chat_id"] else "NOT_SET"))
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset the single BIST admin account.")
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    password = sys.stdin.read()
    reset_admin(args.username, password)


if __name__ == "__main__":
    main()
