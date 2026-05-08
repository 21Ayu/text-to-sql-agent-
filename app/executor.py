"""
Executes SQL queries against either an in-memory SQLite connection (Excel source)
or a live MySQL engine (SQLAlchemy). Always returns a pandas DataFrame.
"""

import sqlite3
import pandas as pd
from sqlalchemy.engine import Engine
from sqlalchemy import text

from app.errors import classify_error


class QueryExecutor:
    def __init__(self, source_type: str, sqlite_conn: sqlite3.Connection = None, mysql_engine: Engine = None):
        """
        source_type: "excel" | "mysql"
        """
        self.source_type = source_type
        self.sqlite_conn = sqlite_conn
        self.mysql_engine = mysql_engine

    def run(self, sql: str) -> pd.DataFrame:
        if self.source_type == "excel":
            return self._run_sqlite(sql)
        elif self.source_type == "mysql":
            return self._run_mysql(sql)
        else:
            raise ValueError(f"Unknown source_type: {self.source_type}")

    def _run_sqlite(self, sql: str) -> pd.DataFrame:
        if self.sqlite_conn is None:
            raise RuntimeError("No data loaded — please upload an Excel file in the sidebar.")
        try:
            return pd.read_sql_query(sql, self.sqlite_conn)
        except Exception as e:
            raise RuntimeError(classify_error(str(e), context="sql_exec")) from e

    def _run_mysql(self, sql: str) -> pd.DataFrame:
        if self.mysql_engine is None:
            raise RuntimeError("No database connected — configure MySQL connection in the sidebar.")
        try:
            with self.mysql_engine.connect() as conn:
                return pd.read_sql_query(text(sql), conn)
        except Exception as e:
            raise RuntimeError(classify_error(str(e), context="sql_exec")) from e
