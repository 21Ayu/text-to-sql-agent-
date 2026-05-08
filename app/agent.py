"""
LLM agent that converts natural language to SQL and detects chart requests.
Supports three providers: Claude (Anthropic), OpenAI, and Ollama (local/free).
"""

import os
import json
import re
from app.executor import QueryExecutor

SYSTEM_PROMPT = """You are a data analyst assistant. You have access to a database with the following schema:

{schema}

SQL Dialect: {dialect}

Rules:
- Always use the exact table and column names from the schema above.
- For date filtering, use standard SQL date functions appropriate for the dialect.
- Return only the columns needed to answer the question.
- For aggregations, always use aliases (e.g., SUM(sales) AS total_sales).
- Never use DROP, DELETE, UPDATE, INSERT, or any DDL statements.

You MUST respond with a valid JSON object and nothing else. No explanation, no markdown, just the JSON.

JSON format:
{{
  "sql": "<the SQL query>",
  "needs_chart": <true or false>,
  "chart_type": "<bar|line|pie|scatter|area|none>",
  "x_axis": "<column name for x axis or pie labels>",
  "y_axis": "<column name for y axis or pie values>",
  "title": "<short descriptive title>"
}}
"""

# ── OpenAI function/tool schema (used for OpenAI and Ollama with tool support) ──
QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_data",
        "description": "Generate SQL and detect chart requirements from a natural language question.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Valid SQL query for the given dialect."},
                "needs_chart": {"type": "boolean", "description": "True if user asked for a chart."},
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie", "scatter", "area", "none"],
                },
                "x_axis": {"type": "string", "description": "Column for X axis or pie labels."},
                "y_axis": {"type": "string", "description": "Column for Y axis or pie values."},
                "title": {"type": "string", "description": "Short title for the result."},
            },
            "required": ["sql", "needs_chart", "chart_type", "x_axis", "y_axis", "title"],
        },
    },
}


def _build_system(schema: str, dialect: str) -> str:
    return SYSTEM_PROMPT.format(schema=schema, dialect=dialect)


def _extract_json(text: str) -> dict:
    """Parse JSON from model response, stripping any markdown fences."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


# ── Provider: Claude ─────────────────────────────────────────────────────────

def _call_claude(messages: list, schema: str, dialect: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    CLAUDE_TOOL = {
        "name": "query_data",
        "description": "Generate SQL and detect chart requirements.",
        "input_schema": QUERY_TOOL["function"]["parameters"],
    }

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_build_system(schema, dialect),
        tools=[CLAUDE_TOOL],
        tool_choice={"type": "any"},
        messages=messages,
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "query_data":
            return block.input
    raise RuntimeError("Claude did not return a structured tool call.")


# ── Provider: OpenAI ─────────────────────────────────────────────────────────

def _call_openai(messages: list, schema: str, dialect: str, model: str) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    full_messages = [{"role": "system", "content": _build_system(schema, dialect)}] + messages
    response = client.chat.completions.create(
        model=model,
        tools=[QUERY_TOOL],
        tool_choice={"type": "function", "function": {"name": "query_data"}},
        messages=full_messages,
    )
    tool_call = response.choices[0].message.tool_calls[0]
    return json.loads(tool_call.function.arguments)


# ── Provider: Ollama ─────────────────────────────────────────────────────────

def _call_ollama(messages: list, schema: str, dialect: str, model: str, base_url: str) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key="ollama", base_url=base_url)

    full_messages = [{"role": "system", "content": _build_system(schema, dialect)}] + messages

    # Try function calling first; fall back to JSON parsing
    try:
        response = client.chat.completions.create(
            model=model,
            tools=[QUERY_TOOL],
            tool_choice={"type": "function", "function": {"name": "query_data"}},
            messages=full_messages,
        )
        tool_calls = response.choices[0].message.tool_calls
        if tool_calls:
            return json.loads(tool_calls[0].function.arguments)
    except Exception:
        pass

    # Fallback: ask for raw JSON response
    response = client.chat.completions.create(
        model=model,
        messages=full_messages,
        temperature=0,
    )
    return _extract_json(response.choices[0].message.content)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_agent(
    user_question: str,
    schema: str,
    executor: QueryExecutor,
    dialect: str = "SQLite",
    chat_history: list = None,
    provider: str = "openai",          # "claude" | "openai" | "ollama"
    openai_model: str = "gpt-4o-mini",
    ollama_model: str = "llama3",
    ollama_base_url: str = "http://localhost:11434/v1",
) -> dict:
    messages = list(chat_history or [])
    messages.append({"role": "user", "content": user_question})

    last_error = None
    for attempt in range(2):
        try:
            if provider == "claude":
                tool_input = _call_claude(messages, schema, dialect)
            elif provider == "openai":
                tool_input = _call_openai(messages, schema, dialect, openai_model)
            elif provider == "ollama":
                tool_input = _call_ollama(messages, schema, dialect, ollama_model, ollama_base_url)
            else:
                raise ValueError(f"Unknown provider: {provider}")

            sql = tool_input["sql"]
            df = executor.run(sql)
            return {
                "sql": sql,
                "dataframe": df,
                "needs_chart": tool_input.get("needs_chart", False),
                "chart_type": tool_input.get("chart_type", "none"),
                "x_axis": tool_input.get("x_axis", ""),
                "y_axis": tool_input.get("y_axis", ""),
                "title": tool_input.get("title", "Query Result"),
                "error": None,
            }
        except Exception as e:
            last_error = str(e)
            if attempt == 0:
                messages.append({
                    "role": "assistant",
                    "content": f"The previous SQL failed with: {last_error}. Please fix and retry.",
                })

    return {
        "sql": "", "dataframe": None, "needs_chart": False,
        "chart_type": "none", "x_axis": "", "y_axis": "",
        "title": "", "error": last_error,
    }
