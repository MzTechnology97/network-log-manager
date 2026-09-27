import pymysql

from .config import ENV


def app_db():
    return pymysql.connect(
        host=ENV["NETLOG_DB_HOST"],
        user=ENV["NETLOG_DB_USER"],
        password=ENV["NETLOG_DB_PASSWORD"],
        database=ENV["NETLOG_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def syslog_db():
    return pymysql.connect(
        host=ENV["SYSLOG_DB_HOST"],
        user=ENV["SYSLOG_DB_USER"],
        password=ENV["SYSLOG_DB_PASSWORD"],
        database=ENV["SYSLOG_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
