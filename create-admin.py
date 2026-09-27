#!/opt/netlog-manager/venv/bin/python3

import getpass
import os
import sys
from pathlib import Path

import pymysql
from argon2 import PasswordHasher
from argon2.exceptions import HashingError


ENV_FILE = Path(os.environ.get("NETLOG_ENV_FILE", "/opt/netlog-manager/config/app.env"))


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
    env = dict(os.environ)
    if ENV_FILE.is_file():
        for key, value in load_env(ENV_FILE).items():
            env.setdefault(key, value)
