import os
from pathlib import Path

ENV_FILE = Path(os.environ.get("NETLOG_ENV_FILE", "/opt/netlog-manager/config/app.env"))

def load_env():
    values = dict(os.environ)
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return values

ENV = load_env()
