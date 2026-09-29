import pymysql
from zoneinfo import ZoneInfo

from .config import ENV


def _session_time_zone():
    """Return the configured local UTC offset for this DB session.

    MariaDB installations do not always ship timezone-name tables, so using a
    numeric offset keeps NOW()/CURRENT_TIMESTAMP consistent with NETLOG_TIMEZONE
    in both Native and Docker deployments.
    """
    zone = ZoneInfo(ENV.get("NETLOG_TIMEZONE", ENV.get("TZ", "Europe/Rome")))
    offset = __import__("datetime").datetime.now(zone).utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _connect(**kwargs):
    conn = pymysql.connect(**kwargs)
    with conn.cursor() as cur:
        cur.execute("SET time_zone=%s", (_session_time_zone(),))
    return conn


def app_db():
    return _connect(
        host=ENV["NETLOG_DB_HOST"],
        user=ENV["NETLOG_DB_USER"],
        password=ENV["NETLOG_DB_PASSWORD"],
        database=ENV["NETLOG_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def syslog_db():
    return _connect(
        host=ENV["SYSLOG_DB_HOST"],
        user=ENV["SYSLOG_DB_USER"],
        password=ENV["SYSLOG_DB_PASSWORD"],
        database=ENV["SYSLOG_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
