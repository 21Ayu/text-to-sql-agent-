"""
Extracts schema metadata from Excel files and MySQL databases.
Returns a structured string for injecting into the LLM prompt.
No hardcoded schema — everything is derived dynamically from the data source.
"""

import re
import pandas as pd
from urllib.parse import quote_plus
from sqlalchemy import create_engine, inspect, text


def sanitize_table_name(name: str) -> str:
    """Convert a sheet/table name to a safe SQLite identifier.
    Strips browser download suffixes like ' (2)', then replaces special chars with underscores.
    """
    # Remove trailing browser download version like " (2)", " (3)", etc.
    clean = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
    if not clean:
        clean = name
    # Replace any run of non-alphanumeric characters with a single underscore
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", clean).strip("_")
    # Prefix with t_ if it starts with a digit
    if safe and safe[0].isdigit():
        safe = "t_" + safe
    return safe or "table"


def _pandas_type_to_sql(dtype) -> str:
    dtype_str = str(dtype)
    if "int" in dtype_str:
        return "INTEGER"
    if "float" in dtype_str:
        return "REAL"
    if "datetime" in dtype_str:
        return "DATETIME"
    if "bool" in dtype_str:
        return "BOOLEAN"
    return "TEXT"


def _sample_values(series: pd.Series, max_unique: int = 8) -> str:
    try:
        unique = series.dropna().unique()
        if len(unique) == 0:
            return "no values"
        samples = [str(v) for v in unique[:max_unique]]
        suffix = "..." if len(unique) > max_unique else ""
        return f"[{', '.join(samples)}{suffix}]"
    except Exception:
        return ""


def extract_excel_schema(file_path: str) -> dict:
    """
    Reads all sheets from an Excel file.
    Returns a rich schema string + tables dict.
    Schema includes column types, sample values, stats, and detected cross-sheet relationships.
    """
    xl = pd.ExcelFile(file_path)
    tables = {}

    for sheet in xl.sheet_names:
        df = pd.read_excel(file_path, sheet_name=sheet)
        df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]
        safe_name = sanitize_table_name(sheet)
        tables[safe_name] = df

    schema_lines = ["=== DATABASE SCHEMA (auto-extracted from Excel) ===", ""]

    # Detect potential relationships: column names shared across multiple sheets
    all_cols: dict[str, list[str]] = {}
    for tbl, df in tables.items():
        for col in df.columns:
            all_cols.setdefault(col.lower(), []).append(tbl)
    relationships = {col: sheets for col, sheets in all_cols.items() if len(sheets) > 1}

    for tbl, df in tables.items():
        schema_lines.append(f"## Table: {tbl} ({len(df)} rows)")
        schema_lines.append("Columns:")

        for col in df.columns:
            series = df[col]
            sql_type = _pandas_type_to_sql(series.dtype)
            null_pct = round(series.isna().mean() * 100, 1)
            unique_count = series.nunique(dropna=True)

            col_info = f"  - {col} ({sql_type}) | unique: {unique_count} | nulls: {null_pct}%"

            if sql_type in ("TEXT", "BOOLEAN") and unique_count <= 20:
                col_info += f" | values: {_sample_values(series)}"
            elif sql_type in ("INTEGER", "REAL"):
                try:
                    mn, mx = series.min(), series.max()
                    col_info += f" | range: {mn:.4g}–{mx:.4g}"
                except Exception:
                    pass
            elif sql_type == "DATETIME":
                try:
                    col_info += f" | range: {series.min().date()} to {series.max().date()}"
                except Exception:
                    pass
            else:
                col_info += f" | samples: {_sample_values(series, max_unique=3)}"

            schema_lines.append(col_info)

        schema_lines.append("")

    if relationships:
        schema_lines.append("## Potential Relationships (shared column names across sheets)")
        for col, sheets in relationships.items():
            schema_lines.append(f"  - '{col}' found in: {', '.join(sheets)}")
        schema_lines.append("")

    return {"schema_str": "\n".join(schema_lines), "tables": tables}


def extract_mysql_schema(engine) -> str:
    """
    Extracts MySQL schema dynamically — no hardcoded tables or columns.
    """
    inspector = inspect(engine)
    schema_lines = ["=== DATABASE SCHEMA (auto-extracted from MySQL) ===", ""]

    for table_name in inspector.get_table_names():
        columns = inspector.get_columns(table_name)
        schema_lines.append(f"## Table: {table_name}")
        schema_lines.append("Columns:")
        for col in columns:
            schema_lines.append(f"  - {col['name']} ({col['type']})")
        schema_lines.append("")

    return "\n".join(schema_lines)


def extract_value_reference(engine, table_names: list) -> str:
    """
    Samples actual distinct values for text-like, low-cardinality columns
    in the user-selected tables only. Returns a reference block for the LLM prompt.
    """
    if not table_names:
        return ""

    inspector = inspect(engine)
    all_db_tables = set(inspector.get_table_names())

    _SKIP_KEYWORDS = {
        "name", "email", "address", "description", "note", "comment",
        "remark", "token", "hash", "uuid", "url", "path", "key", "secret",
        "message", "body", "content", "text",
    }

    lines = ["=== COLUMN VALUES (sampled from selected tables) ==="]

    with engine.connect() as conn:
        for table_name in table_names:
            if table_name not in all_db_tables:
                continue
            columns = inspector.get_columns(table_name)
            for col in columns:
                col_name = col["name"]
                col_type_str = str(col["type"]).upper()

                if not any(t in col_type_str for t in ("VARCHAR", "CHAR", "TEXT", "ENUM", "TINYINT")):
                    continue
                if any(kw in col_name.lower() for kw in _SKIP_KEYWORDS):
                    continue

                try:
                    row = conn.execute(
                        text(f"SELECT COUNT(DISTINCT `{col_name}`) FROM `{table_name}`")
                    ).fetchone()
                    if row and row[0] <= 25:
                        vals = conn.execute(
                            text(
                                f"SELECT DISTINCT `{col_name}` FROM `{table_name}` "
                                f"WHERE `{col_name}` IS NOT NULL LIMIT 25"
                            )
                        ).fetchall()
                        vals_list = [str(r[0]) for r in vals if r[0] is not None]
                        if vals_list:
                            lines.append(f"  {table_name}.{col_name}: {vals_list}")
                except Exception:
                    pass

    return "\n".join(lines) if len(lines) > 1 else ""


def build_mysql_engine(host: str, port: int, user: str, password: str, database: str):
    url = f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"
    return create_engine(url)
