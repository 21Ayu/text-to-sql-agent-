# Text-to-SQL AI Agent — Project Context

## What This Project Is
A Streamlit-based AI agent that converts natural language business questions into SQL queries, executes them, and returns results as tables or interactive charts. Built for non-technical users to query any MySQL database or uploaded Excel file without writing SQL. The app is a **blank model** — it derives all schema knowledge dynamically from the data source and from an optional user-uploaded context document (txt or pdf). No schema is hardcoded anywhere.

## How to Run
```bash
cd C:\Users\akaur\Desktop\Text_to_sql
streamlit run app/main.py
```
Opens at http://localhost:8501

## Project Structure
```
Text_to_sql/
├── app/
│   ├── main.py              # Streamlit UI — entry point
│   ├── graph_agent.py       # LangGraph agent (relevance → RAG → SQL gen → execute)
│   ├── rag_retriever.py     # ChromaDB RAG indexer for user-uploaded context docs
│   ├── schema_extractor.py  # Auto-extracts rich schema from Excel or MySQL
│   ├── data_loader.py       # Loads Excel sheets into in-memory SQLite
│   ├── executor.py          # Runs SQL on SQLite (Excel) or MySQL
│   ├── chart_renderer.py    # Plotly chart renderer (bar/line/pie/scatter/area)
│   ├── errors.py            # Central error classifier — maps exceptions to friendly messages
│   └── agent.py             # Old direct-API agent (unused, can be deleted)
├── uploads/
│   ├── excel/               # Persisted uploaded Excel files (auto-reloaded on restart)
│   └── context/             # Persisted context documents (txt/pdf, auto-reloaded)
├── chroma_db/               # ChromaDB vector store (auto-created on first context upload)
├── requirements.txt
├── .env                     # API keys and DB credentials (never commit this)
└── CLAUDE.md                # This file
```

## Architecture

### LangGraph Agent Flow
```
User question
    ↓
[check_relevance]   — LLM classifier: is this a data/SQL question or off-topic?
    ↓
[retrieve_context]  — RAG search on user-uploaded context doc (~50ms, no LLM call)
    ↓                 Returns relevant chunks from the uploaded txt/pdf
[generate_sql]      — 1 LLM call with full auto-extracted schema + RAG context + DB values
    ↓                 Returns {sql, needs_chart, chart_type, x_axis, y_axis, title}
[conditional edge]
    success → END
    error + retries < 2 → back to generate_sql with error context
    error + no retries → END with friendly error message
```

### Schema Auto-Extraction (no hardcoding)
At connect/upload time the schema is dynamically extracted and includes:
- **Excel**: per-column type, unique count, null %, actual values for low-cardinality columns (≤20 unique), range for numerics/dates, 3 samples for high-cardinality text, cross-sheet relationship detection (shared column names)
- **MySQL**: per-column type. Optionally, users select which tables to scan for enum values in the sidebar — this runs `SELECT DISTINCT` on low-cardinality text columns and injects exact values into the prompt.

### RAG System (user-supplied only)
- **Source**: any `.txt` or `.pdf` file uploaded by the user in the sidebar
- **Chunking**: splits on double-newlines (paragraphs)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector DB**: ChromaDB persisted in `chroma_db/`
- **On upload**: file saved to `uploads/context/`, indexed into ChromaDB
- **On restart**: ChromaDB cache reloaded automatically (no re-indexing needed)
- **On replace**: user confirms, old index cleared, new file indexed
- **If no key / no upload**: RAG is silently skipped; the full schema alone is used

### Session Persistence
All uploads are saved to disk and auto-reloaded on browser refresh or server restart:
- Excel file → `uploads/excel/` (only one at a time)
- Context document → `uploads/context/` (only one at a time)
- ChromaDB index survives restarts by design (persisted to `chroma_db/`)

### Error Handling
All errors pass through `app/errors.py` → `classify_error(raw, context)` which maps raw exception messages to single-line user-friendly strings. Errors are surfaced at:
- Sidebar: MySQL connect failures, Excel load failures
- Chat: LLM errors, SQL execution errors, chart rendering errors, empty results

### SQL Safety
`generate_sql` node checks generated SQL for forbidden keywords (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `CREATE`, `ALTER`, `TRUNCATE`, `REPLACE`, `GRANT`, `REVOKE`) and blocks before execution.

### Data Sources
The sidebar "Data Source" section has two tabs — **Excel** and **MySQL** — that are mutually exclusive (connecting one replaces the other as the active source).
- **Excel**: uploaded via sidebar → saved to `uploads/excel/` → loaded into in-memory SQLite → same SQL execution path as MySQL. Persisted to disk and auto-reloaded on restart.
- **MySQL**: connected via SQLAlchemy + PyMySQL. The MySQL tab shows a connection form (host/port/user/password/database, pre-filled from `.env`), a Connect button, a schema viewer, the Column Value Sampling expander, and a Disconnect button. Connections are **not** persisted across restarts (credentials are never written to disk) — reconnect each session. The `dialect` passed to the agent is `"MySQL"` when MySQL is active, `"SQLite"` for Excel.

## LLM Providers Supported
Configured in sidebar at runtime — no code change needed:

| Provider | Model | Notes |
|---|---|---|
| OpenAI | gpt-4o-mini (default), gpt-4o, gpt-3.5-turbo | Recommended. Key in `.env` as `OPENAI_API_KEY`. Also used for RAG embeddings. |
| Ollama | llama3, phi3:mini, tinyllama | Free, local. Needs `ollama serve` running. RAG requires separate OpenAI key. |
| Claude | claude-sonnet-4-6 | Requires separate Anthropic API key (not Claude Pro) |

## Environment Variables (.env)
```
ANTHROPIC_API_KEY=...     # Anthropic API (optional)
OPENAI_API_KEY=...        # OpenAI API key — required for RAG embeddings
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=...
MYSQL_DATABASE=newdata
```

## Key Files to Edit for Tuning

### Improve schema understanding for Excel → `app/schema_extractor.py`
- `extract_excel_schema()` — adjust `max_unique` threshold (default 20) for showing values vs samples
- The schema is auto-generated and injected into every prompt — no manual editing needed

### Improve schema understanding for MySQL → sidebar
- Use the "Column Value Sampling" expander (visible once connected) to select tables
- Click "Sample values" to inject actual enum/status values into prompts

### Provide business rules / domain context → sidebar upload
- Upload a `.txt` or `.pdf` explaining what tables mean, column business rules, example questions
- This is the ONLY way to inject custom context — no hardcoded files
- Tip: include example Q&A pairs like "Q: How many orders? SQL: SELECT COUNT(*) FROM orders"

### Change prompt rules → `app/graph_agent.py`
- `_make_generate_sql()` — SQL generation prompt rules and forbidden keyword list
- `_make_check_relevance()` — off-topic detection examples
- `_make_retrieve_context()` — adjust `top_k` for RAG retrieval (default 5)

### Add error message mappings → `app/errors.py`
- `classify_error(raw, context)` — add new patterns to map exception text to friendly messages

### Change chart rendering → `app/chart_renderer.py`
- Uses Plotly Express
- Supports: bar, line, pie, scatter, area

## Known Issues / Decisions
- `app/agent.py` is the old single-call agent kept for reference — not used, can be deleted
- ChromaDB `chroma_db/` folder must not be committed to version control
- Password special characters (e.g. `@`) in MySQL URL are handled via `urllib.parse.quote_plus`
- Ollama with llama3 requires ~8GB RAM — use `phi3:mini` or `tinyllama` on low-RAM machines
- Claude Pro subscription does NOT include API access — needs separate console.anthropic.com account
- RAG embeddings require OpenAI API key even when using Ollama or Claude as the LLM
- The `–` (en-dash) character in schema output is UTF-8; displays correctly in Streamlit but may appear as `?` in Windows terminals using cp1252

## Dependencies
```
streamlit, python-dotenv, langchain-core, langchain-openai, langchain-anthropic,
langchain-chroma, langgraph, chromadb, openai, anthropic, pandas, openpyxl,
sqlalchemy, pymysql, plotly, pypdf, pydantic
```

Install: `pip install -r requirements.txt`

## What Has Been Built (Session History)
1. Phase 1: Core scaffold — schema extractor, SQLite data loader, SQL executor, direct Claude agent, Streamlit UI
2. Phase 2: Multi-provider support — OpenAI and Ollama added alongside Claude
3. Phase 3: Business context — `schema_context.yaml` injected into LLM prompt for domain understanding
4. Phase 4: LangGraph refactor — 3-node graph (retrieve_context → generate_sql → execute_sql) with auto-retry
5. Phase 5: RAG integration — ChromaDB + OpenAI embeddings, schema_description.txt chunked by table
6. Phase 6: Enum value enforcement — `extract_value_reference()` queries real DISTINCT values at connect time
7. Phase 7: Error handling — `app/errors.py` central classifier, friendly single-line messages
8. Phase 8: Query examples — `query_examples.txt` indexed by RAG alongside schema
9. **Phase 9: Blank-model refactor** — removed all hardcoded schema files (`schema_description.txt`, `query_examples.txt`, `schema_context.yaml`). Schema is now fully auto-extracted from the data source with rich metadata. RAG now indexes user-uploaded documents (txt/pdf) only. MySQL enum sampling is user-driven via sidebar multiselect. All uploads persisted to disk and auto-reloaded on restart. Context replace requires user confirmation.
10. **Phase 10: MySQL exposed in UI** — the Data Source sidebar is now tabbed (Excel / MySQL). The MySQL tab provides a connection form (pre-filled from `.env`), Connect/Disconnect, schema viewer, and Column Value Sampling. `run_agent` is called with `dialect="MySQL"` and the sampled `value_reference` when MySQL is active. Backend (executor, schema_extractor, build_mysql_engine) already supported MySQL — this wired it into `app/main.py`.

## Possible Next Features
- Export query results to CSV/Excel download button
- Query history panel showing past questions and SQL
- Admin dashboard with preset/pinned questions
- Support for PostgreSQL as a third data source
- Add more chart types or PNG chart download
- Support multiple context documents (currently one at a time)
