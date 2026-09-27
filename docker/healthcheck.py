#!/usr/bin/env python3
import sys
import urllib.request

try:
    from app.database import app_db
    conn = app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        conn.close()
    with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP health returned {response.status}")
except Exception as exc:
    print(f"unhealthy: {exc}", file=sys.stderr)
    sys.exit(1)
