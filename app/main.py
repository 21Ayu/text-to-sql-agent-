"""
Streamlit UI for the Text-to-SQL AI Agent.
Connect a data source — upload an .xlsx file or connect a live MySQL database —
then ask questions in plain English.
Context is provided entirely by the user via an uploaded document (txt/pdf).
All uploaded files are persisted to disk and reloaded automatically on restart.
"""

import sys
import os
import re
import glob

# Ensure project root is on the path before any app.* imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from dotenv import load_dotenv

from app.data_loader import reload_excel
from app.executor import QueryExecutor
from app.graph_agent import run_agent
from app.chart_renderer import render_chart
from app.rag_retriever import RAGRetriever
from app.errors import classify_error
from app.schema_extractor import (
    extract_mysql_schema,
    extract_value_reference,
    build_mysql_engine,
)
from sqlalchemy import inspect as sa_inspect

load_dotenv()

# Load Streamlit Cloud secrets into os.environ (no-op locally)
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

# ── Upload directories (persisted across sessions) ────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADS_DIR = os.path.join(_PROJECT_ROOT, "uploads")
_EXCEL_DIR = os.path.join(_UPLOADS_DIR, "excel")
_CONTEXT_DIR = os.path.join(_UPLOADS_DIR, "context")

for _d in (_EXCEL_DIR, _CONTEXT_DIR):
    os.makedirs(_d, exist_ok=True)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Text-to-SQL Agent",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 Text-to-SQL AI Agent")
st.caption("Ask business questions in plain English. Get SQL, tables, and charts.")

# ── Session state defaults ────────────────────────────────────────────────────
_STATE_DEFAULTS = {
    "source_type": None,
    "sqlite_conn": None,
    "schema": None,
    "executor": None,
    "retriever": None,
    "rag_status": "",
    "chat_history": [],
    "messages": [],
    "openai_model": "gpt-4o-mini",
    # MySQL connection state
    "mysql_engine": None,
    "mysql_tables": [],
    "value_reference": "",
    "persisted_mysql_db": None,
    # Persistence state
    "persisted_excel_name": None,
    "persisted_context_name": None,
    "context_full_text": "",
    "confirm_replace_context": False,
    "_auto_load_done": False,
    "_just_loaded_excel": None,
    "_excel_uploader_key": 0,
}
for key, default in _STATE_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ── Helpers ───────────────────────────────────────────────────────────────────

def _openai_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "")


def _csv_download_button(df, title: str, key: str) -> None:
    """Render a CSV download button for a DataFrame."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_").lower() or "query_result"
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download CSV",
        data=csv_bytes,
        file_name=f"{slug}.csv",
        mime="text/csv",
        key=key,
    )


def _copy_sql_button(sql: str, key: str) -> None:
    """Offer the generated SQL for copy/reuse.

    The code block above already has Streamlit's native copy-to-clipboard icon
    (top-right on hover). This adds an explicit, always-reliable way to grab the
    query as a .sql file — robust across Streamlit versions, unlike a custom JS
    clipboard button (which needs the now-deprecated components.html iframe)."""
    st.caption("Use the copy icon on the code block above, or download the query:")
    st.download_button(
        label="⬇ Download .sql",
        data=sql.encode("utf-8"),
        file_name="generated_query.sql",
        mime="application/sql",
        key=key,
    )


def _build_retriever(openai_key: str = None) -> RAGRetriever:
    return RAGRetriever(
        embedding_provider="openai",
        openai_api_key=openai_key or _openai_key(),
    )


def _save_uploaded_file(uploaded_file, target_dir: str) -> str:
    """Save a Streamlit UploadedFile to target_dir, return the saved path."""
    # Clear old files in that dir first
    for old in glob.glob(os.path.join(target_dir, "*")):
        try:
            os.remove(old)
        except Exception:
            pass
    dest = os.path.join(target_dir, uploaded_file.name)
    with open(dest, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return dest


def _load_excel_file(file_path: str) -> bool:
    """Load an Excel file from disk into session state. Returns True on success."""
    try:
        conn, schema_str, tables = reload_excel(file_path)
        st.session_state.update(
            source_type="excel",
            sqlite_conn=conn,
            schema=schema_str,
            executor=QueryExecutor("excel", sqlite_conn=conn),
            persisted_excel_name=os.path.basename(file_path),
            chat_history=[],
            messages=[],
        )
        return True
    except Exception as e:
        st.error(classify_error(str(e), context="excel"))
        return False


def _connect_mysql(host: str, port: str, user: str, password: str, database: str) -> bool:
    """Connect to MySQL, extract schema, and wire up the executor. Returns True on success."""
    # Validate inputs before attempting a connection so users get clear guidance.
    if not host.strip():
        st.error("Host is required — enter the MySQL server address.")
        return False
    if not database.strip():
        st.error("Database is required — enter the database name.")
        return False
    port = port.strip()
    if not (port.isdigit() and 1 <= int(port) <= 65535):
        st.error("Port must be a number between 1 and 65535.")
        return False
    try:
        engine = build_mysql_engine(host, int(port), user, password, database)
        schema_str = extract_mysql_schema(engine)  # connects + inspects (raises if unreachable)
        tables = sa_inspect(engine).get_table_names()
        st.session_state.update(
            source_type="mysql",
            mysql_engine=engine,
            schema=schema_str,
            executor=QueryExecutor("mysql", mysql_engine=engine),
            mysql_tables=tables,
            value_reference="",
            persisted_mysql_db=database,
            chat_history=[],
            messages=[],
        )
        return True
    except Exception as e:
        st.error(classify_error(str(e), context="db_connect"))
        return False


def _disconnect_mysql() -> None:
    """Tear down the MySQL connection and clear related state."""
    engine = st.session_state.mysql_engine
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            pass
    st.session_state.update(
        source_type=None,
        mysql_engine=None,
        schema=None,
        executor=None,
        mysql_tables=[],
        value_reference="",
        persisted_mysql_db=None,
        chat_history=[],
        messages=[],
    )


def _index_context_file(file_path: str) -> None:
    """Index a context file into RAG and update session state."""
    r = _build_retriever()
    status = r.index_file(file_path, source_name=os.path.basename(file_path))
    st.session_state.retriever = r
    st.session_state.rag_status = status
    st.session_state.persisted_context_name = os.path.basename(file_path)
    # Keep the full document text for context-only SQL generation.
    try:
        st.session_state.context_full_text = r.read_full_text(file_path)
    except Exception:
        st.session_state.context_full_text = ""


def _clear_context() -> None:
    """Remove persisted context file and clear RAG index."""
    for old in glob.glob(os.path.join(_CONTEXT_DIR, "*")):
        try:
            os.remove(old)
        except Exception:
            pass
    if st.session_state.retriever:
        st.session_state.retriever.clear()
    st.session_state.retriever = None
    st.session_state.rag_status = ""
    st.session_state.persisted_context_name = None
    st.session_state.context_full_text = ""
    st.session_state.confirm_replace_context = False


# ── Auto-load persisted files on fresh session ────────────────────────────────
if not st.session_state._auto_load_done:
    st.session_state._auto_load_done = True

    # Reload persisted Excel
    excel_files = sorted(glob.glob(os.path.join(_EXCEL_DIR, "*.xlsx")), key=os.path.getmtime)
    if excel_files and st.session_state.executor is None:
        _load_excel_file(excel_files[-1])

    # Reload persisted RAG context (from ChromaDB cache)
    ctx_files = sorted(glob.glob(os.path.join(_CONTEXT_DIR, "*")), key=os.path.getmtime)
    if ctx_files and st.session_state.retriever is None and _openai_key():
        r = _build_retriever()
        status = r.load_existing()
        if r._vectorstore is not None:
            st.session_state.retriever = r
            st.session_state.rag_status = status
            st.session_state.persisted_context_name = os.path.basename(ctx_files[-1])
            # Recover full document text for context-only SQL generation.
            try:
                st.session_state.context_full_text = r.read_full_text(ctx_files[-1])
            except Exception:
                st.session_state.context_full_text = ""


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:

    # ── Model selection ───────────────────────────────────────────────────────
    st.session_state.openai_model = st.selectbox(
        "Model", ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"], index=0
    )

    st.divider()

    # ── Data Source ───────────────────────────────────────────────────────────
    st.header("Data Source")

    excel_tab, mysql_tab = st.tabs(["Excel", "MySQL"])

    # ── Excel ─────────────────────────────────────────────────────────────────
    with excel_tab:
        if st.session_state.persisted_excel_name and st.session_state.source_type == "excel":
            st.success(f"Loaded: {st.session_state.persisted_excel_name}")
            with st.expander("Schema"):
                st.code(st.session_state.schema or "")
            if st.button("Clear Excel file"):
                for old in glob.glob(os.path.join(_EXCEL_DIR, "*")):
                    try:
                        os.remove(old)
                    except Exception:
                        pass
                st.session_state.update(
                    source_type=None, sqlite_conn=None, schema=None,
                    executor=None, persisted_excel_name=None,
                    chat_history=[], messages=[],
                )
                st.session_state._excel_uploader_key += 1
                st.rerun()

        uploaded = st.file_uploader(
            "Upload Excel (.xlsx)",
            type=["xlsx"],
            key=f"excel_uploader_{st.session_state._excel_uploader_key}",
        )
        if uploaded and uploaded.name != st.session_state.persisted_excel_name:
            size_mb = uploaded.size / (1024 * 1024)
            if size_mb > 50:
                st.error(
                    f"**Large file ({size_mb:.1f} MB)** — this may exhaust available memory on "
                    "Streamlit Cloud and cause a crash. Consider trimming the file to under 50 MB."
                )
            elif size_mb > 20:
                st.warning(
                    f"**Moderate file size ({size_mb:.1f} MB)** — loading may take 15–30 seconds."
                )
            saved_path = _save_uploaded_file(uploaded, _EXCEL_DIR)
            if _load_excel_file(saved_path):
                st.session_state._just_loaded_excel = uploaded.name
                st.session_state._excel_uploader_key += 1
                st.rerun()

    # ── MySQL ─────────────────────────────────────────────────────────────────
    with mysql_tab:
        if st.session_state.source_type == "mysql" and st.session_state.mysql_engine is not None:
            st.success(f"Connected: {st.session_state.persisted_mysql_db}")
            with st.expander("Schema"):
                st.code(st.session_state.schema or "")

            with st.expander("Column Value Sampling"):
                st.caption(
                    "Select tables to sample real enum/status values from. "
                    "These exact values are injected into the prompt to improve filtering."
                )
                sel_tables = st.multiselect(
                    "Tables", st.session_state.mysql_tables, key="mysql_sample_tables"
                )
                if st.button("Sample values"):
                    with st.spinner("Sampling column values..."):
                        st.session_state.value_reference = extract_value_reference(
                            st.session_state.mysql_engine, sel_tables
                        )
                    if st.session_state.value_reference:
                        st.success("Values sampled and added to prompt context.")
                    else:
                        st.info("No low-cardinality text columns found in the selected tables.")
                if st.session_state.value_reference:
                    st.code(st.session_state.value_reference)

            if st.button("Disconnect"):
                _disconnect_mysql()
                st.rerun()
        else:
            with st.form("mysql_connect_form"):
                host = st.text_input("Host", value=os.environ.get("MYSQL_HOST", "localhost"))
                port = st.text_input("Port", value=os.environ.get("MYSQL_PORT", "3306"))
                user = st.text_input("User", value=os.environ.get("MYSQL_USER", "root"))
                password = st.text_input(
                    "Password", value=os.environ.get("MYSQL_PASSWORD", ""), type="password"
                )
                database = st.text_input("Database", value=os.environ.get("MYSQL_DATABASE", ""))
                connect = st.form_submit_button("Connect", type="primary")
            if connect:
                with st.spinner("Connecting to MySQL..."):
                    ok = _connect_mysql(host, port, user, password, database)
                if ok:
                    st.rerun()

    st.divider()

    # ── Context Document (RAG) ────────────────────────────────────────────────
    st.header("Context Document")
    st.caption(
        "Upload a .txt or .pdf file containing business rules, column descriptions, "
        "domain terminology, or example questions. Used to improve SQL generation."
    )

    if st.session_state.persisted_context_name:
        st.success(f"Loaded: {st.session_state.persisted_context_name}")
        if st.session_state.rag_status:
            st.caption(st.session_state.rag_status)
        if st.button("Clear context document"):
            _clear_context()
            st.rerun()

    uploaded_ctx = st.file_uploader(
        "Upload context (.txt or .pdf)",
        type=["txt", "pdf"],
        key="ctx_uploader",
    )

    if uploaded_ctx:
        has_existing = bool(st.session_state.persisted_context_name)

        if has_existing and not st.session_state.confirm_replace_context:
            st.warning(
                f"This will replace the current context "
                f"'{st.session_state.persisted_context_name}'. Confirm?"
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Yes, replace", type="primary"):
                    st.session_state.confirm_replace_context = True
                    st.rerun()
            with col2:
                if st.button("Cancel"):
                    st.rerun()
        else:
            # Index the new file
            with st.spinner("Indexing context document..."):
                saved_ctx_path = _save_uploaded_file(uploaded_ctx, _CONTEXT_DIR)
                _index_context_file(saved_ctx_path)
            st.session_state.confirm_replace_context = False
            st.success(f"Context indexed: {uploaded_ctx.name}")
            st.caption(st.session_state.rag_status)
            st.rerun()

    st.divider()
    if st.button("Clear conversation"):
        st.session_state.chat_history = []
        st.session_state.messages = []
        st.rerun()


# ── Main chat area ────────────────────────────────────────────────────────────
# Context-only mode: no data source, but a context document is loaded — the agent
# generates SQL from the document alone and shows it without executing.
_generate_only = (
    st.session_state.executor is None
    and bool(st.session_state.context_full_text)
)

if st.session_state.executor is None and not _generate_only:
    st.info("Configure a data source in the sidebar to get started.")
    if not st.session_state.persisted_context_name:
        st.info(
            "Optionally upload a context document in the sidebar to help the model "
            "understand your data (business rules, column meanings, example questions)."
        )
    st.stop()

# Show one-time banner after Excel upload
if st.session_state._just_loaded_excel:
    st.success(
        f"**'{st.session_state._just_loaded_excel}' is ready!** "
        "Ask a question below to start querying your data."
    )
    st.session_state._just_loaded_excel = None

if _generate_only:
    st.info(
        "**SQL generation only** — no data source connected. The agent will write "
        "MySQL queries from your context document, but they won't be executed. "
        "Connect Excel or MySQL in the sidebar to run queries and see results."
    )

# Render existing messages
for _msg_idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
            if msg.get("generate_only"):
                st.code(msg["sql"], language="sql")
                _copy_sql_button(msg["sql"], key=f"copy_{_msg_idx}")
            else:
                with st.expander("Generated SQL"):
                    st.code(msg["sql"], language="sql")
        if msg.get("fig") is not None:
            st.plotly_chart(msg["fig"], use_container_width=True)
        if msg.get("df") is not None:
            st.dataframe(msg["df"], use_container_width=True)
            _csv_download_button(
                msg["df"],
                title=msg.get("title", "query_result"),
                key=f"dl_{_msg_idx}",
            )

# Chat input
question = st.chat_input("Ask a business question...")
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = run_agent(
                user_question=question,
                schema=st.session_state.context_full_text if _generate_only else st.session_state.schema,
                executor=None if _generate_only else st.session_state.executor,
                dialect="MySQL" if (_generate_only or st.session_state.source_type == "mysql") else "SQLite",
                chat_history=st.session_state.chat_history,
                openai_model=st.session_state.openai_model,
                retriever=st.session_state.retriever,
                value_reference=st.session_state.value_reference,
            )

        if not result.get("is_relevant", True):
            msg = (
                "I can only answer questions about your data — "
                "try asking about your tables, records, or metrics."
            )
            st.warning(msg)
            st.session_state.messages.append({"role": "assistant", "content": msg})

        elif result["error"]:
            friendly = classify_error(result["error"])
            st.error(friendly)
            if result.get("sql"):
                with st.expander("Generated SQL (failed)"):
                    st.code(result["sql"], language="sql")
            st.session_state.messages.append({"role": "assistant", "content": f"Error: {friendly}"})

        elif _generate_only:
            # No data source — show the generated SQL only.
            st.code(result["sql"], language="sql")
            _copy_sql_button(result["sql"], key="copy_inline")
            content = f"**{result['title']}** — generated SQL (not executed; no data source connected)."
            st.markdown(content)
            st.session_state.messages.append({
                "role": "assistant",
                "content": content,
                "sql": result["sql"],
                "generate_only": True,
                "title": result["title"],
            })
            st.session_state.chat_history.append({"role": "user", "content": question})
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": f"SQL: {result['sql']}",
            })

        else:
            df = result["dataframe"]
            fig = None

            with st.expander("Generated SQL"):
                st.code(result["sql"], language="sql")

            row_count = len(df) if df is not None else 0

            if row_count == 0:
                st.info("Query ran successfully but returned no rows — try broadening your filters or date range.")
            elif result["needs_chart"] and result["chart_type"] != "none":
                try:
                    fig = render_chart(
                        df,
                        chart_type=result["chart_type"],
                        x_axis=result["x_axis"],
                        y_axis=result["y_axis"],
                        title=result["title"],
                    )
                    st.plotly_chart(fig, use_container_width=True)
                except Exception as e:
                    st.warning(classify_error(str(e), context="chart"))
                    fig = None
                st.dataframe(df, use_container_width=True)
                _csv_download_button(df, title=result["title"], key="dl_inline")
            else:
                st.dataframe(df, use_container_width=True)
                _csv_download_button(df, title=result["title"], key="dl_inline")

            content = (
                f"**{result['title']}** — no rows returned."
                if row_count == 0
                else f"**{result['title']}** — {row_count} row{'s' if row_count != 1 else ''} returned."
            )
            st.markdown(content)

            st.session_state.messages.append({
                "role": "assistant",
                "content": content,
                "sql": result["sql"],
                "df": df,
                "fig": fig,
                "title": result["title"],
            })
            st.session_state.chat_history.append({"role": "user", "content": question})
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": f"SQL: {result['sql']}\nReturned {row_count} rows.",
            })
