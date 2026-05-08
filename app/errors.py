"""
Central error classifier. Converts raw exception messages into a single-line
user-friendly string that explains what went wrong and what to do about it.
"""

import re


def classify_error(raw: str, context: str = "") -> str:
    """
    Map a raw exception message to a short, actionable user-facing string.
    context hint: "llm" | "db_connect" | "sql_exec" | "chart" | "excel" | ""
    """
    r = raw.lower()

    # ── LLM / API key errors ──────────────────────────────────────────────────
    if any(x in r for x in ("401", "invalid_api_key", "incorrect api key",
                             "invalid x-api-key", "authentication_error",
                             "invalid api key", "no api key provided")):
        return "API key is invalid — update it in the sidebar under LLM Provider."

    if any(x in r for x in ("429", "rate_limit_exceeded", "rate limit",
                             "too many requests")):
        return "LLM rate limit hit — wait a few seconds and try again."

    if any(x in r for x in ("insufficient_quota", "quota exceeded",
                             "exceeded your current quota", "billing")):
        return "LLM quota exceeded — check your billing at the provider's dashboard."

    if ("connection refused" in r or "could not connect" in r) and (
            "11434" in raw or "ollama" in r):
        return "Ollama is not running — open a terminal and run: ollama serve"

    if ("model" in r and "not found" in r and "ollama" not in r and "404" not in raw):
        pass  # fall through — might be SQL table error
    elif ("404" in raw and ("ollama" in r or "11434" in raw)) or (
            "model" in r and "not found" in r and ("ollama" in r or "11434" in raw)):
        return "Ollama model not found — run: ollama pull <model-name> in a terminal."

    if "timed out" in r or ("timeout" in r and "sql" not in r):
        return "LLM request timed out — try a simpler question or switch to a faster model."

    if "overloaded" in r or "529" in raw:
        return "LLM provider is overloaded — wait a moment and try again."

    # ── MySQL connect errors ──────────────────────────────────────────────────
    if "access denied for user" in r:
        return "Database login failed — check your username and password in the sidebar."

    if "unknown database" in r:
        m = re.search(r"unknown database '(.+?)'", r)
        db = f" '{m.group(1)}'" if m else ""
        return f"Database{db} not found — verify the database name in the sidebar."

    if ("can't connect to" in r or "connection refused" in r
            or "2003" in raw or "nodename nor servname" in r
            or "name or service not known" in r):
        return "Cannot reach MySQL server — check host and port in the sidebar."

    if "lost connection" in r or "server has gone away" in r or "2006" in raw:
        return "Database connection was lost — reconnect using the sidebar."

    if "ssl" in r and ("certificate" in r or "handshake" in r):
        return "MySQL SSL error — disable SSL or add the correct certificate."

    # ── SQL execution errors ──────────────────────────────────────────────────
    if "table" in r and "doesn't exist" in r:
        m = re.search(r"table '(.+?)' doesn't exist", r)
        table = m.group(1).split(".")[-1] if m else "unknown"
        return f"Table `{table}` not found — try rephrasing the question or check your data source."

    if "unknown column" in r:
        m = re.search(r"unknown column '(.+?)'", r)
        col = f"`{m.group(1)}`" if m else "a column"
        return f"Column {col} not found — try rephrasing the question."

    if "you have an error in your sql syntax" in r or (
            "syntax error" in r and "sql" in r):
        return "Generated SQL has a syntax error — try rephrasing the question."

    if "division by zero" in r:
        return "Query caused a division by zero — try adding a filter to exclude zero values."

    if "command denied" in r or ("denied" in r and "command" in r):
        return "Database user lacks SELECT permission — contact your DBA."

    if "no such table" in r:
        m = re.search(r"no such table: (\S+)", r)
        table = m.group(1) if m else "unknown"
        return f"Table `{table}` not found — try rephrasing the question or check your data source."

    if "no such column" in r:
        m = re.search(r"no such column: (\S+)", r)
        col = m.group(1) if m else "unknown"
        return f"Column `{col}` not found — try rephrasing the question."

    if "query was empty" in r:
        return "LLM returned an empty query — try rephrasing the question."

    # ── Chart errors ─────────────────────────────────────────────────────────
    if "not found in result" in r or ("columns" in r and "not found" in r):
        return "Chart column not found in results — try asking the question without requesting a chart."

    if "empty dataframe" in r or (context == "chart" and "empty" in r):
        return "No data to chart — the query returned 0 rows."

    if "could not convert" in r or "unsupported operand" in r:
        return "Chart requires numeric values — the selected column contains text data."

    # ── Excel errors ─────────────────────────────────────────────────────────
    if any(x in r for x in ("openpyxl", "xlrd", "zipfile", "excel", "bad magic number")):
        return "Excel file could not be read — ensure it is a valid .xlsx file."

    if "no sheets" in r or "sheet" in r and "not found" in r:
        return "Excel file has no readable sheets — upload a file with at least one data sheet."

    # ── RAG / embedding errors ────────────────────────────────────────────────
    if "embeddings" in r and ("empty" in r or "non-empty" in r):
        return "Embedding failed — set a valid OpenAI API key to enable RAG retrieval."

    # ── SQL safety (our own messages, already friendly) ───────────────────────
    if raw.startswith("Query blocked"):
        return raw

    # ── Generic fallback — truncate long technical messages ──────────────────
    short = raw.split("\n")[0].strip()
    if len(short) > 130:
        short = short[:127] + "…"
    return short
