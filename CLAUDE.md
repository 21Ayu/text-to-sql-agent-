# Text-to-SQL AI Agent — Project Context

## What This Project Is
A Streamlit-based AI agent that converts natural language business questions into SQL queries, executes them, and returns results as tables or interactive charts. Built for non-technical users to query any MySQL database or uploaded Excel file without writing SQL. The app is a **blank model** by default — it derives schema knowledge dynamically from the data source and from an optional user-uploaded context document (txt or pdf). It can also run in a **curated product-vertical mode** where a pre-built context file (per product line) is loaded so the model generates SQL without any data connection. No schema is hardcoded in application code; curated schema lives in editable `verticals/*.txt` files.

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
├── verticals/               # Curated, app-bundled context per product line (committed)
│   ├── insurance.txt        # Seeded from a real schema
│   ├── challan.txt          # Starter template — fill with real schema
│   ├── fastag.txt           # Starter template — fill with real schema
│   ├── car_loan.txt         # Starter template — fill with real schema
│   └── parking.txt          # Starter template — fill with real schema
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
[conditional edge: _route_after_generate]
    executor is None (context-only) → END  (return SQL without executing)
    executor present                → execute_sql
[execute_sql]
[conditional edge: _should_retry]
    success → END
    error + retries < 2 → back to generate_sql with error context
    error + no retries → END with friendly error message
```

### Context-Only Mode (generate SQL without a data source)
If **no data source** is connected but a **context document is uploaded**, the app runs in
*generate-only* mode: it writes SQL from the context document alone and displays it without
executing (there is nothing to run it against).
- Triggered in `app/main.py` by `_generate_only = executor is None and bool(context_full_text)`.
- The full context document text (not just RAG top-k chunks) is passed as the `schema` —
  `RAGRetriever.read_full_text()` reads the whole txt/pdf.
- Dialect is fixed to **MySQL** in this mode.
- `run_agent(executor=None)` → `_route_after_generate` ends the graph after `generate_sql`,
  skipping execution and retries. Output is the generated SQL only (no table/chart/CSV),
  shown in a code block (with Streamlit's native copy icon) plus an explicit
  "Download .sql" button (`_copy_sql_button`). A custom JS clipboard button was avoided
  because it requires the deprecated `components.html` iframe.

### Product Verticals (curated, app-bundled context)
The sidebar **Context** section lets a user pick a **product vertical** (Insurance, Challan,
Fastag, Car Loan, Parking) instead of uploading their own doc. This trades the "blank model"
generality for curated, consistent per-vertical quality — a deliberate product choice.
- Files live in `verticals/<name>.txt` and are **committed** to the repo. `insurance.txt`
  is seeded from a real schema; the other four are starter templates to be filled in.
- `VERTICALS` config in `app/main.py` maps each vertical → its context file, with a `db`
  slot reserved for the future **live-DB-per-vertical** phase (currently `None`).
- Selecting a vertical (`_load_vertical`) injects the **full file text** as the context
  (no RAG/embeddings) and runs **generate-only** mode → produces MySQL SQL, not executed.
- Verticals and custom upload are mutually exclusive: loading a vertical supersedes any
  uploaded context (clears `uploads/context/` + RAG index); uploading clears `active_vertical`.
- **Phase 1 = generate-only**; once confidence is high, populate each vertical's `db` slot
  to auto-connect its MySQL database and execute queries for real.

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
10. **Phase 10: MySQL exposed in UI** — the Data Source sidebar is now tabbed (Excel / MySQL). The MySQL tab provides a connection form (pre-filled from `.env`), Connect/Disconnect, schema viewer, and Column Value Sampling. `run_agent` is called with `dialect="MySQL"` and the sampled `value_reference` when MySQL is active. Backend (executor, schema_extractor, build_mysql_engine) already supported MySQL — this wired it into `app/main.py`. Also added input validation (host/database required, port 1–65535) before connecting.
11. **Phase 11: Context-only SQL generation** — the app can now generate SQL from an uploaded context document alone, with no Excel/MySQL connection. `graph_agent` accepts `executor=None` and routes straight to END after `generate_sql` (no execution/retries). The full context-doc text is injected as the schema (`RAGRetriever.read_full_text()`), dialect fixed to MySQL, output is SQL only (code block with copy icon + "Download .sql" button). UI gate in `main.py` opens when a context doc is loaded even without a data source. Also pinned `streamlit>=1.56,<2` for reproducible deploys and fixed the context uploader self-replace re-prompt (name guard + uploader key reset).
12. **Phase 12: Product verticals** — added a curated, app-bundled context file per product line (`verticals/insurance.txt` seeded from a real schema; `challan/fastag/car_loan/parking` as starter templates). Sidebar "Context" section gets a vertical selector + "Load vertical context" that injects the full file as context and generates SQL (generate-only, no RAG). Custom upload kept as an override. `VERTICALS` config has a `db` slot reserved for the future live-DB phase.

## Possible Next Features
- Fill the 4 vertical templates (challan/fastag/car_loan/parking) with real schemas
- Live-DB-per-vertical: populate `VERTICALS[...]["db"]` to auto-connect + execute
- Persist the selected vertical across restarts
- Query history panel showing past questions and SQL
- Admin dashboard with preset/pinned questions
- Support for PostgreSQL as a third data source
- Add more chart types or PNG chart download
- Support multiple context documents (currently one at a time)
