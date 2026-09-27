#!/opt/netlog-manager/venv/bin/python3

import getpass
import os
import sys
from pathlib import Path

import pymysql
from argon2 import PasswordHasher
from argon2.exceptions import HashingError


ENV_FILE = Path("/opt/netlog-manager/config/app.env")


def load_env(path):
    values = {}

    for line in path.read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    return values


def main():
    env = load_env(ENV_FILE)

    username = input("Username Administrator: ").strip()

    if not username:
        print("Username non valido.")
        sys.exit(1)

    display_name = input("Nome visualizzato [opzionale]: ").strip() or None
    email = input("Email [opzionale]: ").strip() or None

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Conferma password: ")

    if password != confirm:
        print("Le password non coincidono.")
        sys.exit(1)

    if len(password) < 12:
        print("La password deve contenere almeno 12 caratteri.")
        sys.exit(1)

    ph = PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=2,
        hash_len=32,
        salt_len=16,
    )

    try:
        password_hash = ph.hash(password)
    except HashingError as exc:
        print(f"Errore Argon2: {exc}")
        sys.exit(1)

    conn = pymysql.connect(
        host=env["NETLOG_DB_HOST"],
        user=env["NETLOG_DB_USER"],
        password=env["NETLOG_DB_PASSWORD"],
        database=env["NETLOG_DB_NAME"],
        charset="utf8mb4",
        autocommit=False,
    )

    try:
        with conn.cursor() as cur:

            cur.execute(
                "SELECT id FROM users WHERE username=%s",
                (username,),
            )

            if cur.fetchone():
                print("Username già esistente.")
                conn.rollback()
                sys.exit(1)

            cur.execute(
                """
                INSERT INTO users
                    (
                        username,
                        password_hash,
                        display_name,
                        email,
                        enabled
                    )
                VALUES (%s,%s,%s,%s,1)
                """,
                (
                    username,
                    password_hash,
                    display_name,
                    email,
                ),
            )

            user_id = cur.lastrowid

            cur.execute(
                "SELECT id FROM roles WHERE name='Administrator'"
            )

            role = cur.fetchone()

            if not role:
                raise RuntimeError("Ruolo Administrator non trovato")

            cur.execute(
                """
                INSERT INTO user_roles (user_id, role_id)
                VALUES (%s,%s)
                """,
                (user_id, role[0]),
            )

            cur.execute(
                """
                INSERT INTO audit_log
                    (
                        user_id,
                        username,
                        action,
                        category,
                        success,
                        details
                    )
                VALUES
                    (%s,%s,'USER_CREATED','AUTH',1,%s)
                """,
                (
                    user_id,
                    username,
                    "Initial Administrator account created",
                ),
            )

        conn.commit()

        print()
        print("Administrator creato correttamente.")
        print(f"User ID: {user_id}")
        print(f"Username: {username}")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
