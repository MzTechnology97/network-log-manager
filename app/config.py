from pathlib import Path


ENV_FILE = Path("/opt/netlog-manager/config/app.env")


def load_env():
    values = {}

    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    return values


ENV = load_env()
