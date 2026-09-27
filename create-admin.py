#!/usr/bin/env python3
import getpass
import os
import sys
import pymysql
from argon2 import PasswordHasher
from argon2.exceptions import HashingError

def required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

def main():
    print("\n=== Initial Network Log Manager Administrator ===")
    username = input("Administrator username: ").strip()
    if not username:
        raise RuntimeError("Administrator username cannot be empty.")
    display_name = input("Display name (optional): ").strip() or None
    email = input("Email (optional): ").strip() or None

    password = getpass.getpass("Administrator password: ")
    confirm = getpass.getpass("Confirm administrator password: ")
    if password != confirm:
        raise RuntimeError("Passwords do not match.")
    if len(password) < 12:
        raise RuntimeError("Administrator password must be at least 12 characters.")

    try:
        password_hash = PasswordHasher().hash(password)
    except HashingError as exc:
        raise RuntimeError("Unable to hash administrator password.") from exc

    conn = pymysql.connect(
        host=required("NETLOG_DB_HOST"),
        user=required("NETLOG_DB_USER"),
        password=required("NETLOG_DB_PASSWORD"),
        database=required("NETLOG_DB_NAME"),
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", (username,))
            if cur.fetchone():
                raise RuntimeError("That username already exists.")
            if email:
                cur.execute("SELECT id FROM users WHERE email=%s", (email,))
                if cur.fetchone():
                    raise RuntimeError("That email address already exists.")
            cur.execute(
                "INSERT INTO users(username,password_hash,display_name,email,enabled,password_changed_at) "
                "VALUES(%s,%s,%s,%s,1,NOW())",
                (username, password_hash, display_name, email),
            )
            user_id = cur.lastrowid
            cur.execute("SELECT id FROM roles WHERE name='Administrator'")
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Administrator role is missing.")
            cur.execute("INSERT INTO user_roles(user_id,role_id) VALUES(%s,%s)", (user_id, row[0]))
            cur.execute(
                "INSERT INTO audit_log(user_id,username,action,category,target_type,target_id,success,details) "
                "VALUES(%s,%s,'INITIAL_ADMIN_CREATED','AUTH','user',%s,1,'Created by bootstrap installer')",
                (user_id, username, str(user_id)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Administrator '{username}' created successfully.")

if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nAdministrator creation cancelled.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
