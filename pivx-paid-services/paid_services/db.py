from contextlib import closing, contextmanager
from pathlib import Path
import os
import sqlite3


class Database:
    def __init__(self, path):
        self.path = str(path)
        with closing(self.connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported database schema version")
            connection.executescript(Path(__file__).with_name("schema.sql").read_text())
        os.chmod(self.path, 0o600)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rows(self, query, args=()):
        connection = self.connect()
        try:
            return [dict(row) for row in connection.execute(query, args)]
        finally:
            connection.close()
