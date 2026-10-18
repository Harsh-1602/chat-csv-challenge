# CSV Chat Analyst

Upload multiple related CSV files, get an automatic data profile, then chat with the data.

**Flow:** upload → progress bar while the backend loads and profiles every CSV → optional cleaning gate (review detected data-quality issues, then apply fixes or use the data as-is) → chat. Each answer streams its stages live and shows the answer text, the result table or chart, the SQL used, the assumptions, and a CSV download.

## Architecture

| Layer | Tech | Responsibility |
|---|---|---|
| Frontend | React + TypeScript (Vite) | Upload UI, progress tracking, chat screen and dataset panel |
| Backend | FastAPI (Python) | Upload validation, background profiling jobs, status API |
| Engine | DuckDB | Fast CSV ingestion with type inference and SQL-based profiling |

### What profiling computes
Profiling is a pipeline: DuckDB measures, the LLM interprets, and DuckDB verifies again. The model is `gpt-5.6-terra` with medium reasoning by default. With no API key, or if an LLM step fails, a rule-based fallback runs and the profile is marked `degraded`.

1. **Load and statistics (DuckDB):** types, null/distinct counts, numeric stats, top values, duplicates, and single- and composite-key candidates.
2. **Table understanding (LLM, one call per table):** description, grain, entity, primary key, column roles (identifier/measure/time/dimension/boolean/text), units and semantic types. A primary key is accepted only if it is unique in the data.
3. **Relationship candidates (DuckDB):** containment checks with match rate, orphans and cardinality.
4. **Relationship review (LLM):** accept or reject each candidate, plus any extra candidates the LLM proposes. SQL then re-verifies them and flags ambiguous links for review.
5. **Semantic model (LLM):** dataset summary, synonyms, metrics, and example questions. Every metric expression is run through DuckDB and kept only if it executes.
- Row/column counts, inferred types, and a semantic role per column
- Missing values, distinct counts, min/max, mean/median/std, IQR outlier counts, and top categorical values
- Exact duplicate rows, primary-key candidates, and data-quality warnings
- Cross-file relationships (foreign key → primary key), with match rate, orphans, and cardinality

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/sessions` | Saved sessions, newest first (title, status, tables, rows, question count) |
| `POST` | `/api/sessions` | Multipart upload (`files`). Returns `202` with `session_id` and `status_url`; profiling runs in the background |
| `GET` | `/api/sessions/{id}` | Progress (`0–1`), current stage, per-file status, the final `profile`, and any pending cleaning issues |
| `POST` | `/api/sessions/{id}/cleaning` | Apply selected fixes or skip (`409` until profiling is done) |
| `POST` | `/api/sessions/{id}/copy` | Start a new session on the same data with an empty chat, without re-uploading or re-profiling. Returns `201 {session_id}`, `404` for an unknown session, and `409` until profiling is done |
| `POST` | `/api/sessions/{id}/messages` | Ask a question. Server-sent events: `stage`, `step`, `answer`, `error` (`409` while cleaning is pending, `503` without an LLM) |
| `GET` | `/api/sessions/{id}/messages` | Conversation history |
| `GET` | `/api/sessions/{id}/messages/{mid}/download.csv` | Full result of an answer as CSV |
| `GET` | `/api/sessions/{id}/tables/{table}/rows` | Page through a table's rows. Query: `offset`, `limit` (≤ 200), optional `sort` and `desc`. Returns `409` while cleaning is pending and `404` for an unknown table or column |

### Table viewer
Each table card in the chat sidebar has a **View** button. It opens a modal with the table's rows (after cleaning, if any). Columns show their role and type, headers sort ascending → descending → none, and the footer pages through the rows in pages of 25 to 200.

### Chat agent
The chat agent is SQL-first and hybrid: it understands the question (resolving follow-ups and assumptions), plans when needed, then runs a SQL loop with read-only DuckDB, retries and empty-result checks. It then chooses an output (table or chart) and writes a narrative whose numbers are checked against the result. See [docs/chat-design.md](docs/chat-design.md). On the eval it scores 35/35 execution accuracy (`experiments/chat/exp01_hybrid`).

## Running locally

Prerequisites: Python 3.12+ and Node 20+.

```powershell
# Backend (http://localhost:8000)
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000

# Frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

Then open http://localhost:5173 and upload the files in `data/`.

## Free demo deployment

The Render Blueprint builds the React frontend into the Docker image and serves it from the FastAPI app, so the demo uses one URL and one service:

1. In Render, create a **Blueprint** from this GitHub repository and deploy `render.yaml`.
2. When prompted, provide `OPENAI_API_KEY` if you want AI-assisted profiling and chat. Keep the key in Render's environment settings; never commit it.
3. Open the deployed service URL. Check `/api/health` for `{"status":"ok"}`.

This is a public demo without authentication: anyone with the URL can upload files and access sessions. Do not upload sensitive data. The free service has an ephemeral filesystem, so uploads, DuckDB files, and session history can disappear when it sleeps, restarts, or redeploys. The Blueprint limits uploads to 5 CSV files of at most 10 MB each to fit a small demo instance. The LLM provider may charge separately for API usage.

### Configuration (backend environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `STORAGE_DIR` | `backend/storage` | Where uploads and per-session DuckDB files are stored |
| `MAX_FILES` | `20` | Max files per upload |
| `MAX_FILE_SIZE_MB` | `100` | Max size per file |
| `OPENAI_API_KEY` | _(none)_ | Enables AI-assisted profiling; can also be set in a repo-root `.env` |
| `LLM_ENABLED` | `true` | Set `false` to force rule-based profiling |
| `OPENAI_MODEL` | `gpt-5.6-terra` | Model used for profiling |
| `OPENAI_REASONING_EFFORT` | `medium` | Reasoning effort |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed browser origins |

## Tests

```powershell
cd backend
.\.venv\Scripts\python -m pytest
cd ..\frontend
npm run build   # type-check + production build
```

## Evaluation datasets

`eval/` contains open multi-table datasets (Jaffle Shop, Chinook, TPC-H) with natural-language questions, gold SQL and generated expected answers. See [eval/README.md](eval/README.md).

## Design docs

- [docs/architecture.md](docs/architecture.md): **start here.** The full system architecture: the approach, design patterns and SOLID mapping, the end-to-end lifecycle, the profiling, cleaning and chat workflows with their data flow, the chat's hybrid orchestration and SSE streaming, session state and persistence, the API, security, and scaling.
- [docs/chat-design.md](docs/chat-design.md): chat agent decisions, the C0–C8 pipeline and evaluation.
- [docs/profiling-design.md](docs/profiling-design.md): profiling v2 design. The LLM is used at several stages (table understanding, relationship decisions, semantic model), with SQL checking each LLM result.
- [docs/profiling-research.md](docs/profiling-research.md): research on data profiling (key and relationship discovery, profiling tools, semantic types, schema context for text-to-SQL, join fan-out), the agent pipeline overview, and a gap analysis of the current profiler.
- [experiments/](experiments/README.md): numbered experiments for each agent stage. Each has its strategy, code, runs and results, plus a leaderboard against the [profiling baseline](experiments/profiling/README.md).

## Code layout (backend)

- `app/llm/`: LLM access only. Contains the `LLMClient` protocol, the OpenAI implementation, a `TrackedLLM` decorator that counts calls, tokens and timings, and the `build_llm_client` factory.
- `app/profiling/`: profiling steps, each in its own module with one responsibility: statistics, candidate finding, link verification, metric validation and key resolution. The LLM-facing steps (interpreter, judge, semantic builder) are protocols with LLM and heuristic implementations. `factory.py` is the composition root that picks implementations from settings. `pipeline.py` orchestrates the steps and handles fallback.
- `app/cleaning/`: issue detection and fixes that are applied only when the user approves them. The cleaned table replaces the original, which is kept as `<table>__raw`, and the semantic model is refreshed.
- `app/chat/`: the chat agent (understander, planner, SQL loop, output selector, narrator, grounding, read-only executor), plus prompts and context building.
- `app/tables.py` and `app/databases.py`: paged table browsing, and a registry of shared read-only DuckDB connections used by both chat and the table viewer.
- `app/jobs.py`, `app/conversations.py`, `app/streaming.py` and `app/main.py`: background jobs, conversation store, SSE encoding and HTTP routes.
- `app/persistence.py`: the `SessionRepository` protocol and the `JsonSessionRepository` implementation, which writes `session.json` and `conversation.json` into each session folder under `storage/sessions/<id>/` using atomic writes. `JobStore` and `ConversationService` depend only on the protocol.

### Session history
- Sessions and their conversations are saved to disk and restored when the backend starts. Profiling that was interrupted by a restart is marked as failed.
- The frontend puts the open session in the URL (`#/session/<id>`). Reloading the page, sharing the link, or using back/forward reopens that session.
- Recent sessions are listed on the upload screen and in a **Sessions** drawer in the chat view. Each session is titled with its first question.
- **New chat on the same data.** The chat top bar and every completed session in the list have a **New chat** button. It copies the session's files, database, profile and cleaning state into a new session with an empty conversation, so there is no re-upload and no re-profiling. The two sessions are independent.

Score the integrated pipeline on the eval datasets: `backend\.venv\Scripts\python experiments\profiling\app_pipeline\run.py [--heuristic]`.

## Known limitations
- Session state is stored as JSON files on local disk. Multi-instance deployments need a shared store (for example Postgres) behind `SessionRepository`, plus a job queue.
- Profiling that is interrupted by a restart is not resumed, so the files must be uploaded again. Sessions created before persistence was added are not listed.
- Sessions cannot be deleted from the UI or API. The cleaning decision is made once per session. There is no authentication, so every user sees every session.
- Uploaded files are not yet cleaned up automatically.
