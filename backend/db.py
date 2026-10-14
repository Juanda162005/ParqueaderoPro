import os
import re
import sqlite3
from typing import Any

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


class DatabaseRow(dict):
    def __getitem__(self, key: Any):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class DatabaseCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, query, params=()):
        if DATABASE_URL:
            query = query.replace("?", "%s")
            query = re.sub(r"^\s*INSERT\s+OR\s+IGNORE", "INSERT", query, flags=re.I)
        self._cursor.execute(query, params)
        if DATABASE_URL and query.lstrip().upper().startswith("INSERT"):
            match = re.search(r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", query, re.I)
            if match:
                self._cursor.execute(
                    "SELECT currval(pg_get_serial_sequence(%s, 'id')) AS id",
                    (match.group(1),),
                )
                row = self._cursor.fetchone()
                self.lastrowid = row["id"] if isinstance(row, dict) else row[0]
        else:
            self.lastrowid = getattr(self._cursor, "lastrowid", None)
        return self

    def executemany(self, query, params):
        if DATABASE_URL:
            query = query.replace("?", "%s")
        self._cursor.executemany(query, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if DATABASE_URL and row is not None and not isinstance(row, DatabaseRow):
            return DatabaseRow(row)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        if DATABASE_URL:
            return [DatabaseRow(row) for row in rows]
        return rows

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cursor.rowcount


class DatabaseConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return DatabaseCursor(self._connection.cursor())

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def connect_db():
    if not DATABASE_URL:
        connection = sqlite3.connect(
            os.getenv("DB_FILE", os.path.join(os.path.dirname(__file__), "parqueadero.db"))
        )
        connection.row_factory = sqlite3.Row
        return DatabaseConnection(connection)
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return DatabaseConnection(psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor))
