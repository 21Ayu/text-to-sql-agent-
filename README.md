# Text-to-SQL AI Agent

A Streamlit web app that lets non-technical users query any Excel dataset or MySQL database using plain English. Connect your data, ask questions, and get instant tables and charts — no SQL knowledge required.

---

## What It Does

Connect an Excel file or a live MySQL database and ask questions like:

> *"Show me total revenue by region for last quarter"*
> *"Which product category has the highest return rate?"*
> *"Give me a bar chart of top 10 customers by order value"*

The agent writes the SQL, runs it, and returns the result as an interactive table or chart — all in seconds.

---

## Real-World Use Cases

**Sales & Revenue Analysis**
Operations and sales teams often live in Excel. Instead of waiting for a data analyst to write queries, a sales manager can upload the monthly sales export and immediately ask: *"Which sales rep closed the most deals this month?"* or *"What's the revenue trend week over week?"*

**E-commerce & Retail**
Merchandising teams can drop in order/inventory exports and ask: *"Which SKUs are below reorder level?"*, *"What's the average order value by payment method?"*, or *"Show a pie chart of orders by fulfilment status."*

**Finance & Operations**
Finance teams can query expense reports, budget files, or reconciliation sheets in plain English without touching formulas. Ask: *"Total spend by department"*, *"Flag transactions above $10,000"*, or *"Month-over-month variance in operating costs."*

**HR & People Analytics**
HR teams can analyze headcount exports, attrition data, or survey results without SQL skills: *"How many employees joined vs left each quarter?"*, *"Average tenure by department"*, *"Headcount breakdown by location."*

**Executive Reporting**
Instead of building static dashboards, executives can upload a weekly data dump and ask ad-hoc questions themselves, getting instant charts ready for presentations.

---

## How It Works

```
User question
    ↓
[Relevance check]   — Is this a data question or off-topic?
    ↓
[Context retrieval] — Fetch relevant business rules from uploaded context doc (RAG)
    ↓
[SQL generation]    — GPT writes SQL using the auto-extracted schema + context
    ↓
[Execution]         — SQL runs against the data; auto-retries on error (up to 2x)
    ↓
Table + Chart returned to user
```

**Schema is fully automatic.** The agent reads your data source and extracts column types, sample values, value ranges, null rates, and cross-sheet relationships — no configuration needed. For MySQL, you can optionally sample real column values (enum/status fields) so the agent filters on exact values.

**Business context via RAG.** Upload a `.txt` or `.pdf` file (e.g., a data dictionary, business rules doc, or list of example questions). The agent retrieves relevant chunks and injects them into every prompt, so it understands domain-specific terminology and column meanings.

---

## Tech Stack

| Layer | Technology |
|---|---|
| UI | Streamlit |
| Agent orchestration | LangGraph |
| LLM | OpenAI GPT (gpt-4o / gpt-4o-mini) |
| RAG embeddings | OpenAI text-embedding-3-small |
| Vector store | ChromaDB |
| SQL engine | SQLite (in-memory, from Excel) / MySQL (live, via SQLAlchemy + PyMySQL) |
| Charts | Plotly Express |
| Excel parsing | pandas + openpyxl |

---

## Project Structure

```
Text_to_sql/
├── app/
│   ├── main.py              # Streamlit UI — entry point
│   ├── graph_agent.py       # LangGraph 4-node pipeline
│   ├── rag_retriever.py     # ChromaDB RAG indexer
│   ├── schema_extractor.py  # Auto-extracts schema from Excel or MySQL
│   ├── data_loader.py       # Loads Excel into in-memory SQLite
│   ├── executor.py          # Runs SQL on SQLite or MySQL, returns DataFrame
│   ├── chart_renderer.py    # Plotly chart builder
│   └── errors.py            # Maps exceptions to friendly messages
├── verticals/               # Curated context per product line (insurance, challan, fastag, car_loan, parking)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Running Locally

**1. Clone the repo**
```bash
git clone https://github.com/21Ayu/text-to-sql-agent-.git
cd text-to-sql-agent-
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Set your API key**
```bash
cp .env.example .env
# Edit .env and add your OpenAI API key
```

**4. Run**
```bash
streamlit run app/main.py
```

Opens at `http://localhost:8501`

---

## Usage

1. **Connect a data source** in the sidebar — choose a tab:
   - **Excel** — upload an `.xlsx` file (multi-sheet files are supported)
   - **MySQL** — enter host, port, user, password, and database, then click **Connect**. Once connected, optionally use **Column Value Sampling** to inject real enum/status values into the prompt for more accurate filtering.
2. **Add context** in the sidebar — either:
   - **Pick a product vertical** (Insurance / Challan / Fastag / Car Loan / Parking) and click **Load vertical context** to use its built-in schema, or
   - *(Optional)* **Upload your own context document** (`.txt` or `.pdf`) with business rules or a data dictionary
3. **Ask a question** in the chat box
4. Get back a table, chart, or both — with the generated SQL visible in an expander
5. **Download results** as CSV with one click

> Excel and MySQL are mutually exclusive — connecting one replaces the other as the active source. MySQL connections are not persisted across restarts (credentials are never written to disk); reconnect each session.

### Product verticals

The app ships with curated schema context per product line in `verticals/`. Pick a vertical in the sidebar and the model generates SQL for that domain with **no data connection required** (generate-only — SQL is shown, not executed). `insurance.txt` is a complete example; the other four are starter templates — edit the matching `verticals/*.txt` file with your real schema (no code change needed).

### Context-only mode (no data source)

You don't have to connect a database at all. If you upload **only a context document** (`.txt` or `.pdf`) describing your tables/columns, the agent will generate **MySQL** queries from that document and display the SQL — without executing it (there's no database to run against). Copy the query with the code block's copy icon or grab it with the **Download .sql** button. This is useful for drafting queries against a schema you don't have loaded locally. Connect Excel or MySQL whenever you want those queries to actually run and return results.

---

## Features

- **Two data sources** — query an uploaded Excel file or a live MySQL database
- **Product verticals** — pick a curated product line (Insurance, Challan, Fastag, Car Loan, Parking) to generate SQL with no data connection
- **Context-only SQL generation** — with no data source connected, generate MySQL queries from a context document (or vertical) alone (SQL is shown, not executed)
- **Zero configuration** — schema auto-extracted from any Excel file or MySQL database
- **Multi-sheet support** — queries can join data across sheets
- **MySQL value sampling** — optionally inject real enum/status values into the prompt for accurate filtering
- **Chart generation** — bar, line, pie, scatter, area charts on request
- **Auto-retry** — if SQL fails, the agent feeds the error back to the LLM and tries again (up to 2 retries)
- **Off-topic guard** — irrelevant questions are politely declined
- **SQL safety** — blocks all write operations (DROP, DELETE, UPDATE, INSERT, etc.)
- **CSV download** — export any result with one click
- **RAG context** — upload a business rules doc to improve accuracy on domain-specific data

---

## Environment Variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Required — used for SQL generation and RAG embeddings |
| `MYSQL_HOST` | Optional — pre-fills the MySQL connection form (default `localhost`) |
| `MYSQL_PORT` | Optional — pre-fills the MySQL port (default `3306`) |
| `MYSQL_USER` | Optional — pre-fills the MySQL user (default `root`) |
| `MYSQL_PASSWORD` | Optional — pre-fills the MySQL password |
| `MYSQL_DATABASE` | Optional — pre-fills the MySQL database name |

> The MySQL variables only pre-fill the sidebar form for convenience — you can connect to any database by typing the values in the UI. They are not required to run the app.

For Streamlit Cloud deployment, add this in **App Settings → Secrets**:

```toml
OPENAI_API_KEY = "sk-..."
```

---

## Security

- The API key is never exposed in the UI — loaded from server-side secrets only
- Generated SQL is checked for forbidden keywords before execution
- Only `SELECT` queries are permitted; all write operations are blocked
