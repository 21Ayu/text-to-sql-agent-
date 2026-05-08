"""
LangGraph-based Text-to-SQL agent.

Graph flow:
  START
    -> check_relevance  (LLM classifier — is this a data/SQL question?)
    -> [conditional]
         off-topic  -> END (polite refusal)
         on-topic   -> retrieve_context
    -> retrieve_context  (RAG vector search against user-uploaded context doc)
    -> generate_sql      (write SQL using full auto-extracted schema + RAG context)
    -> execute_sql       (run the query)
    -> [conditional]
         success              -> END
         error + retries left -> generate_sql (feed error back, retry)
         error + no retries   -> END (surface error)
"""

import os
from typing import Any, Optional
from typing_extensions import TypedDict

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

from app.executor import QueryExecutor
from app.rag_retriever import RAGRetriever
from app.errors import classify_error


# ── State ─────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    question: str
    dialect: str
    full_schema: str        # auto-extracted schema (always complete)
    focused_schema: str     # same as full_schema — kept for prompt injection
    selected_tables: list   # populated from schema, not RAG
    rag_context: str        # relevant chunks from user-uploaded context document
    sql: str
    dataframe: Optional[Any]
    needs_chart: bool
    chart_type: str
    x_axis: str
    y_axis: str
    title: str
    error: str
    retry_count: int
    chat_history: list
    executor: Any
    retriever: Any          # RAGRetriever instance (may be None)
    value_reference: str    # sampled column values from DB (MySQL only)
    is_relevant: bool


# ── Structured output schemas ─────────────────────────────────────────────────

class RelevanceCheck(BaseModel):
    is_relevant: bool = Field(
        description=(
            "True if the question is asking for data insights, SQL queries, charts, "
            "metrics, trends, counts, or any analysis of the connected database. "
            "False for general knowledge, coding help, personal questions, or anything "
            "unrelated to querying the database."
        )
    )


class SQLOutput(BaseModel):
    sql: str = Field(description="Valid SQL query for the given dialect")
    needs_chart: bool = Field(description="True if user asked for a chart")
    chart_type: str = Field(description="bar, line, pie, scatter, area, or none")
    x_axis: str = Field(description="Column name for x axis or pie labels")
    y_axis: str = Field(description="Column name for y axis or pie values")
    title: str = Field(description="Short descriptive title for the result")


# ── LLM factory ───────────────────────────────────────────────────────────────

def _get_llm(provider, openai_model, ollama_model, ollama_base_url):
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=openai_model, api_key=os.environ.get("OPENAI_API_KEY"), temperature=0)
    elif provider == "ollama":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=ollama_model, base_url=ollama_base_url, api_key="ollama", temperature=0)
    elif provider == "claude":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model="claude-sonnet-4-6", api_key=os.environ.get("ANTHROPIC_API_KEY"), temperature=0)
    raise ValueError(f"Unknown provider: {provider}")


# ── Node: check_relevance ─────────────────────────────────────────────────────

def _make_check_relevance(llm):
    def check_relevance(state: AgentState) -> dict:
        classifier = llm.with_structured_output(RelevanceCheck)
        prompt = f"""You are a gatekeeper for a business data analytics assistant.
The assistant is connected to a database and can only answer questions that require
querying data — things like counts, totals, trends, breakdowns, charts, or any
analysis of records stored in the database.

Classify whether the following question is relevant to data analysis or database querying.

Relevant examples:
- "How many orders were placed today?"
- "Show me revenue trend for last 30 days"
- "Which payment method is most used?"
- "Top 10 users by reward points"
- "Give me a pie chart of orders by status"

Not relevant examples:
- "What is the capital of France?"
- "Write me a Python function"
- "Tell me a joke"
- "What is machine learning?"
- "How are you?"

Question: {state['question']}
"""
        try:
            result = classifier.invoke(prompt)
            return {"is_relevant": result.is_relevant}
        except Exception:
            return {"is_relevant": True}
    return check_relevance


def _route_relevance(state: AgentState) -> str:
    return "retrieve_context" if state.get("is_relevant", True) else END


# ── Node: retrieve_context ────────────────────────────────────────────────────

def _make_retrieve_context():
    def retrieve_context(state: AgentState) -> dict:
        retriever: RAGRetriever = state.get("retriever")

        rag_context = ""
        if retriever is not None and retriever._vectorstore is not None:
            # Enrich query with recent chat context for better retrieval
            history_str = " ".join(
                msg.get("content", "")
                for msg in (state.get("chat_history") or [])[-2:]
            )
            query = f"{state['question']} {history_str}".strip()
            rag_context = retriever.retrieve_as_context(query, top_k=5)

        return {
            "focused_schema": state["full_schema"],
            "selected_tables": [],
            "rag_context": rag_context,
        }
    return retrieve_context


# ── Node: generate_sql ────────────────────────────────────────────────────────

_FORBIDDEN = (
    "drop ", "delete ", "update ", "insert ",
    "create ", "alter ", "truncate ", "replace ",
    "grant ", "revoke ",
)


def _make_generate_sql(llm):
    def generate_sql(state: AgentState) -> dict:
        structured_llm = llm.with_structured_output(SQLOutput)

        error_ctx = ""
        if state.get("error") and state.get("retry_count", 0) > 0:
            error_ctx = f"""
The previous SQL failed with this error:
{state['error']}

Previous SQL:
{state.get('sql', '')}

Fix the query and try again.
"""
        history_str = "".join(
            f"{msg.get('role')}: {msg.get('content')}\n"
            for msg in (state.get("chat_history") or [])[-4:]
        )
        history_block = ("Recent conversation:\n" + history_str) if history_str else ""
        value_ref = state.get("value_reference", "")
        rag_ctx = state.get("rag_context", "")

        prompt = f"""You are an expert data analyst. Write a SQL query to answer the question.

Schema:
{state['focused_schema']}

{rag_ctx}

{value_ref}

SQL Dialect: {state['dialect']}

Rules:
- Use exact table and column names from the schema above.
- CRITICAL: If column values are listed in COLUMN VALUES or RELEVANT CONTEXT sections, use ONLY those exact values — never guess.
- Check the schema for soft-delete columns (is_deleted, soft_delete, is_active) and filter appropriately unless the user asks for deleted records.
- Monetary amounts may be stored in smallest currency units — check the context document for conversion rules.
- Use aliases for aggregated columns e.g. SUM(x) AS total_x.
- Never use DROP, DELETE, UPDATE, INSERT, CREATE, or ALTER.
- For charts set needs_chart=true and pick the best chart type.

{history_block}
{error_ctx}
Question: {state['question']}
"""
        try:
            result = structured_llm.invoke(prompt)
        except Exception as e:
            return {
                "sql": "", "needs_chart": False, "chart_type": "none",
                "x_axis": "", "y_axis": "", "title": "",
                "error": classify_error(str(e), context="llm"),
                "retry_count": state.get("retry_count", 0) + 1,
            }

        sql_lower = result.sql.lower().strip()
        for kw in _FORBIDDEN:
            if kw in sql_lower:
                return {
                    "sql": result.sql, "needs_chart": False, "chart_type": "none",
                    "x_axis": "", "y_axis": "", "title": "",
                    "error": f"Query blocked — '{kw.strip()}' statements are not permitted, only SELECT.",
                    "retry_count": 99,
                }

        return {
            "sql": result.sql,
            "needs_chart": result.needs_chart,
            "chart_type": result.chart_type,
            "x_axis": result.x_axis,
            "y_axis": result.y_axis,
            "title": result.title,
            "error": "",
        }
    return generate_sql


# ── Node: execute_sql ─────────────────────────────────────────────────────────

def _make_execute_sql():
    def execute_sql(state: AgentState) -> dict:
        # Don't attempt execution if generate_sql already failed
        if not state.get("sql"):
            return {"dataframe": None}
        try:
            df = state["executor"].run(state["sql"])
            return {"dataframe": df, "error": ""}
        except Exception as e:
            return {
                "dataframe": None,
                "error": str(e),
                "retry_count": state.get("retry_count", 0) + 1,
            }
    return execute_sql


# ── Conditional edges ─────────────────────────────────────────────────────────

def _should_retry(state: AgentState) -> str:
    if state.get("error") and state.get("retry_count", 0) < 2:
        return "generate_sql"
    return END


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph(llm, executor: QueryExecutor):
    graph = StateGraph(AgentState)

    graph.add_node("check_relevance", _make_check_relevance(llm))
    graph.add_node("retrieve_context", _make_retrieve_context())
    graph.add_node("generate_sql", _make_generate_sql(llm))
    graph.add_node("execute_sql", _make_execute_sql())

    graph.add_edge(START, "check_relevance")
    graph.add_conditional_edges(
        "check_relevance",
        _route_relevance,
        {"retrieve_context": "retrieve_context", END: END},
    )
    graph.add_edge("retrieve_context", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_conditional_edges(
        "execute_sql",
        _should_retry,
        {"generate_sql": "generate_sql", END: END},
    )

    return graph.compile()


# ── Public entry point ────────────────────────────────────────────────────────

def run_agent(
    user_question: str,
    schema: str,
    executor: QueryExecutor,
    dialect: str = "SQLite",
    chat_history: list = None,
    provider: str = "openai",
    openai_model: str = "gpt-4o-mini",
    ollama_model: str = "llama3",
    ollama_base_url: str = "http://localhost:11434/v1",
    retriever: Optional[Any] = None,
    value_reference: str = "",
) -> dict:

    llm = _get_llm(provider, openai_model, ollama_model, ollama_base_url)
    compiled = build_graph(llm, executor)

    final = compiled.invoke({
        "question": user_question,
        "dialect": dialect,
        "full_schema": schema,
        "focused_schema": "",
        "selected_tables": [],
        "rag_context": "",
        "sql": "",
        "dataframe": None,
        "needs_chart": False,
        "chart_type": "none",
        "x_axis": "",
        "y_axis": "",
        "title": "Query Result",
        "error": "",
        "retry_count": 0,
        "chat_history": chat_history or [],
        "executor": executor,
        "retriever": retriever,
        "value_reference": value_reference,
        "is_relevant": True,
    })

    return {
        "sql": final.get("sql", ""),
        "dataframe": final.get("dataframe"),
        "needs_chart": final.get("needs_chart", False),
        "chart_type": final.get("chart_type", "none"),
        "x_axis": final.get("x_axis", ""),
        "y_axis": final.get("y_axis", ""),
        "title": final.get("title", "Query Result"),
        "selected_tables": final.get("selected_tables", []),
        "error": final.get("error") or None,
        "is_relevant": final.get("is_relevant", True),
    }
