#!/usr/bin/env python3
import argparse
import getpass
import os
import sys
from pathlib import Path

import pymysql
from argon2 import PasswordHasher

ENV_FILE = Path(os.environ.get("NETLOG_ENV_FILE", "/opt/netlog-manager/config/app.env"))

def load_env(path):
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values

def environment():
    env = dict(os.environ)
    for key, value in load_env(ENV_FILE).items():
        env.setdefault(key, value)
    return env

def connect():
    env = environment()
    return pymysql.connect(
        host=env.get("NETLOG_DB_HOST", "127.0.0.1"),
        port=int(env.get("NETLOG_DB_PORT", "3306")),
        user=env.get("NETLOG_DB_USER", "netlog_app"),
        password=env["NETLOG_DB_PASSWORD"],
        database=env.get("NETLOG_DB_NAME", "netlog_manager"),
        charset="utf8mb4",
        autocommit=False,
    )

def password_pair():
    while True:
        password = getpass.getpass("Administrator password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            continue
        if len(password) < 12:
            print("Password must contain at least 12 characters.", file=sys.stderr)
            continue
        return password

def create_admin():
    username = input("Administrator username: ").strip()
    if not username:
        raise SystemExit("Username cannot be empty.")
    display_name = input("Display name (optional): ").strip() or None
    email = input("Email (optional): ").strip() or None
    password_hash = PasswordHasher().hash(password_pair())
    db = connect()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", (username,))
            if cur.fetchone():
                raise SystemExit("That username already exists.")
            cur.execute(
                "INSERT INTO users(username,password_hash,display_name,email,enabled,password_changed_at) VALUES(%s,%s,%s,%s,1,NOW())",
                (username, password_hash, display_name, email),
            )
            user_id = cur.lastrowid
            cur.execute("SELECT id FROM roles WHERE name='Administrator'")
            role = cur.fetchone()
            if not role:
                raise SystemExit("Administrator role is missing.")
            cur.execute("INSERT INTO user_roles(user_id,role_id) VALUES(%s,%s)", (user_id, role[0]))
            cur.execute(
                "INSERT INTO audit_log(username,action,category,target_type,target_id,success,details) VALUES(%s,'ADMIN_CREATED','AUTH','user',%s,1,'Created by local administration tool')",
                (username, str(user_id)),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(f"Administrator '{username}' created.")

def reset_admin():
    db = connect()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT u.username FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE r.name='Administrator' ORDER BY u.username")
            admins = [row[0] for row in cur.fetchall()]
        if not admins:
            print("No Administrator exists; creating one instead.")
            db.close()
            return create_admin()
        print("Administrators: " + ", ".join(admins))
        username = input("Administrator username to reset: ").strip()
        if username not in admins:
            raise SystemExit("Unknown Administrator username.")
        password_hash = PasswordHasher().hash(password_pair())
        with db.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash=%s,enabled=1,failed_login_count=0,locked_until=NULL,password_changed_at=NOW(),mfa_enabled=0,mfa_secret=NULL WHERE username=%s",
                (password_hash, username),
            )
            cur.execute("UPDATE sessions SET revoked_at=NOW() WHERE user_id=(SELECT id FROM users WHERE username=%s) AND revoked_at IS NULL", (username,))
            cur.execute(
                "INSERT INTO audit_log(username,action,category,target_type,target_id,success,details) VALUES(%s,'ADMIN_PASSWORD_RESET','AUTH','user',%s,1,'Password reset by local administration tool; MFA reset')",
                (username, username),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(f"Credentials reset for Administrator '{username}'. MFA has been disabled and active sessions revoked.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="reset an existing Administrator password")
    args = parser.parse_args()
    reset_admin() if args.reset else create_admin()

if __name__ == "__main__":
    main()
