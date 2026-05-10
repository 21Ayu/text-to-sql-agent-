"""
Streamlit UI for the Text-to-SQL AI Agent.
Excel-only mode — upload an .xlsx file, ask questions in plain English.
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
    # Persistence state
    "persisted_excel_name": None,
    "persisted_context_name": None,
    "confirm_replace_context": False,
    "_auto_load_done": False,
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


def _index_context_file(file_path: str) -> None:
    """Index a context file into RAG and update session state."""
    r = _build_retriever()
    status = r.index_file(file_path, source_name=os.path.basename(file_path))
    st.session_state.retriever = r
    st.session_state.rag_status = status
    st.session_state.persisted_context_name = os.path.basename(file_path)


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


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:

    # ── LLM Provider ──────────────────────────────────────────────────────────
    st.header("LLM Provider")
    openai_key_input = st.text_input(
        "OpenAI API Key",
        type="password",
        value=os.getenv("OPENAI_API_KEY", ""),
        help="Required for SQL generation and RAG embeddings.",
    )
    if openai_key_input:
        os.environ["OPENAI_API_KEY"] = openai_key_input
    st.session_state.openai_model = st.selectbox(
        "Model", ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"], index=0
    )

    st.divider()

    # ── Data Source ───────────────────────────────────────────────────────────
    st.header("Data Source")

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
            st.rerun()

    uploaded = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
    if uploaded:
        saved_path = _save_uploaded_file(uploaded, _EXCEL_DIR)
        if _load_excel_file(saved_path):
            st.success(f"Loaded: {uploaded.name}")
            with st.expander("Schema detected"):
                st.code(st.session_state.schema or "")
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
if st.session_state.executor is None:
    st.info("Configure a data source in the sidebar to get started.")
    if not st.session_state.persisted_context_name:
        st.info(
            "Optionally upload a context document in the sidebar to help the model "
            "understand your data (business rules, column meanings, example questions)."
        )
    st.stop()

# Render existing messages
for _msg_idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
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
                schema=st.session_state.schema,
                executor=st.session_state.executor,
                dialect="SQLite",
                chat_history=st.session_state.chat_history,
                openai_model=st.session_state.openai_model,
                retriever=st.session_state.retriever,
            )

        if not result.get("is_relevant", True):
            msg = (
                "I can only answer questions about the connected data — "
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
