"""
Loads Excel sheets into an in-memory SQLite database so all data sources
share the same SQL execution path.
"""

import sqlite3
import pandas as pd


def load_excel_to_sqlite(tables: dict[str, pd.DataFrame]) -> sqlite3.Connection:
    """
    Takes a dict of {table_name: DataFrame} and loads them into an in-memory
    SQLite connection. Returns the open connection.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    for table_name, df in tables.items():
        df.to_sql(table_name, conn, if_exists="replace", index=False)
    return conn


def reload_excel(file_path: str):
    """
    Re-reads the Excel file and returns fresh tables + a new SQLite connection.
    Used when the user uploads a new file.
    """
    from app.schema_extractor import extract_excel_schema
    result = extract_excel_schema(file_path)
    conn = load_excel_to_sqlite(result["tables"])
    return conn, result["schema_str"], result["tables"]
