# Building "Talk to Your Data" / Text-to-SQL Agents: Research Report

## Executive Summary

- **Schema linking's value is inversely related to LLM strength.** Early pipelines (DIN-SQL, MAC-SQL, CHESS) spent most of their engineering on narrowing the schema; with frontier reasoning models, the "Distillery" line of work (NeurIPS 2024 TRL workshop) argues schema linking can be skipped entirely when the full schema fits in context — our profiling step (small CSVs, pre-summarized columns) puts us squarely in this "skip it" regime.
- **Candidate generation + selection ("self-consistency") is the single highest-leverage technique at the frontier.** CHASE-SQL (ICLR 2025, 73.0% BIRD EX) and XiYan-SQL (~75.6% BIRD, ~89.6% Spider) both win via diverse SQL generation strategies plus a learned/LLM selector, not via a single best prompt.
- **Self-refinement/execution-feedback repair loops are cheap and consistently help**, in both pipelines (MAC-SQL's Refiner: +4.6 pts) and agents (ReFoRCE, CHESS's Unit Tester) — this is a "must-have," not optional, independent of model strength.
- **Spider 2.0 shows real enterprise schemas are still very hard**: GPT-4o scores ~10%, agentic ReFoRCE only reaches ~36% execution score, confirming that production-scale, multi-dialect schemas remain largely unsolved even with agentic tool use — but this gap is mostly about *scale* (1000+ columns, dialect quirks), which does not apply to our PoC's few, profiled CSVs.
- **Agentic (ReAct/tool-calling) loops trade latency/cost for flexibility and robustness to the unexpected**; fixed pipelines trade flexibility for predictability, auditability, and lower latency/cost. Most production systems converge on a **hybrid**: a mostly-deterministic pipeline with one or two bounded "tool" escape hatches (e.g., `describe_table`, repair-on-error).
- **Industry's dominant pattern is "semantic layer + verified queries."** Snowflake Cortex Analyst, Databricks Genie, ThoughtSpot Spotter, Looker (Gemini) and Wren AI all converge on: a human-curated semantic model (YAML/LookML/MDL) + synonyms + a verified-query repository, used either as in-context few-shot or as cache-hits, specifically to fight ambiguity and metric drift. **We already have the semantic-model half of this** — the missing piece is a verified-query cache/repository.
- **Fan-out/chasm-trap double counting is a first-class correctness risk** that generic text-to-SQL benchmarks barely test for, but that vendors treat as core semantic-layer business (Snowflake's "semantic views," LookML symmetric aggregates). This directly matters for our multi-table FK joins with fan-out warnings.
- **Multi-turn handling best practice is "rewrite-to-standalone" over raw history-passing**, confirmed by SParC/CoSQL follow-on work and by how vendor products (ThoughtSpot, Looker) manage conversational state — rewriting is more robust to the "SQL-grounded dialogue state tracking" problem CoSQL identified as still open.
- **Visualization generation is a separate, less mature research area** (LIDA, Chat2VIS, nvBench/nvBench 2.0, VisEval) that mostly validates "LLM + chart-grammar code generation" as viable, with rule-based chart-type heuristics (Mackinlay/Show Me) still a useful fallback/sanity-check rather than something to discard.
- **Ambiguity/abstention is an unsolved, actively-researched problem** (AmbiQT, AMBROSIA, PRACTIQ, TrustSQL, EHRSQL): even strong LLMs fail to recognize ambiguous or unanswerable questions reliably, so **explicit guardrails (clarification turns, confidence/row-count sanity checks, abstention) must be engineered in, not assumed from model quality**.
- **Evaluation should combine execution accuracy on gold-SQL sets (we have Jaffle Shop/Chinook/TPC-H) with LLM-as-judge narration/insight checks and adversarial/robustness probes** (Dr.Spider-style perturbations) — single-number EX accuracy is necessary but insufficient for a "talk to your data" product where narration and chart choice also need grading.

---

## 1. Academic Text-to-SQL State of the Art (2023–2026)

| Method | Year | Core idea | Spider EX | BIRD EX | Notes |
|---|---|---|---|---|---|
| **DIN-SQL** ([arXiv:2304.11015](https://arxiv.org/abs/2304.11015)) | 2023 | Decompose into schema-linking → classification (easy/medium/hard) → generation → self-correction sub-tasks, in-context learning | 85.3% (test) | 55.9% | First major decomposition-based pipeline; self-correction step is a precursor to repair loops |
| **DAIL-SQL** ([arXiv:2308.15363](https://arxiv.org/abs/2308.15363)) | 2023 | Systematic prompt-engineering study: representation format, example selection/organization for few-shot retrieval | 86.6% | — | Established that few-shot example *selection/formatting* matters as much as the model |
| **C3** ([arXiv:2307.07306](https://arxiv.org/abs/2307.07306)) | 2023 | Zero-shot with ChatGPT: clear prompting + calibration bias correction + consistency voting, low token cost | 82.3% (test, zero-shot) | — | Shows strong zero-shot ceiling without fine-tuning or heavy retrieval |
| **MAC-SQL** ([arXiv:2312.11242](https://arxiv.org/abs/2312.11242), [GitHub](https://github.com/wbbeyourself/MAC-SQL)) | 2023/24 | Multi-agent: Selector (schema pruning) → Decomposer (sub-question CoT) → Refiner (execute + fix) | — | 59.6% (test, GPT-4) | Ablations: Refiner alone +4.6 pts, Decomposer +2.1, Selector +3.9 — **refinement is the biggest single lever** |
| **CHESS** ([arXiv:2405.16755](https://arxiv.org/abs/2405.16755), [GitHub](https://github.com/ShayanTalaei/CHESS)) | 2024 | 4 agents: Information Retriever, Schema Selector, Candidate Generator, Unit Tester (LLM-written NL unit tests validate SQL) | — | 71.1% (test) | Up to 83% fewer LLM calls than peers via adaptive schema pruning; near proprietary-model accuracy with open models |
| **CHASE-SQL** ([arXiv:2410.01943](https://arxiv.org/abs/2410.01943), ICLR 2025) | 2024/25 | Diverse candidate generation (divide-and-conquer, query-plan CoT, instance-aware few-shot) + fine-tuned pairwise LLM selector | 87.9%* | **73.0%** (test), 73.01% (dev) | SOTA at publication; selection quality > generation diversity alone |
| **MCS-SQL** ([ACL Anthology, COLING 2025](https://aclanthology.org/2025.coling-main.24/)) | 2024/25 | Multiple prompt variants + multiple-choice LLM selection, schema linking per-prompt | 89.6% | 65.5% (dev) | Exploits LLM prompt-sensitivity as a source of ensemble diversity |
| **RSL-SQL** ([arXiv:2411.00073](https://arxiv.org/abs/2411.00073)) | 2024 | Bidirectional (forward+backward) schema-link pruning, binary candidate selection, multi-turn self-correction | 87.9% | 67.2% (dev, open-source SOTA at the time) | Strong open-weights result; schema-linking recall/precision tradeoff studied explicitly |
| **XiYan-SQL** ([arXiv:2507.04701](https://arxiv.org/abs/2507.04701) / earlier preview [arXiv:2411.08599](https://arxiv.org/abs/2411.08599), [GitHub](https://github.com/alibaba/XiYan-SQL)) | 2024/25 | Multi-generator ensemble (fine-tuned + ICL generators) + "M-Schema" lightweight schema representation + self-refiner + candidate selector | **89.65%** | **75.63%** | De-emphasizes hand-engineered schema linking in favor of a compact schema serialization the LLM itself reasons over |
| **Distillery** ("The Death of Schema Linking? Text-to-SQL in the Age of Well-Reasoned Language Models", NeurIPS 2024 TRL workshop; [OpenReview](https://openreview.net/forum?id=fglyh5pa7d)) | 2024 | Argues explicit schema linking is *unnecessary and risky* when the full schema fits in context — a bad linker can drop needed columns; let a strong, well-reasoning LLM see the whole schema and self-correct instead | — | SOTA at publication time (GPT-4o, no schema linking) | **Central claim directly relevant to our design**: for small/medium schemas, skip dedicated linking, invest instead in good schema serialization and repair |
| **ReFoRCE** ([arXiv:2502.00675](https://arxiv.org/abs/2502.00675), [GitHub — Snowflake Labs](https://github.com/Snowflake-Labs/ReFoRCE)) | 2025 | Agent for **Spider 2.0**: schema compression (pattern-grouping of huge schemas), self-refinement, majority-vote consensus, iterative column exploration via execution feedback | — | Spider 2.0-Snow: 35.83, Spider 2.0-Lite: 36.56 (execution score) | Leads Spider 2.0 leaderboard; plain GPT-4o reportedly ~10% on Spider 2.0, showing how much harder enterprise-scale schemas are |
| **Spider-Agent / Spider 2.0** ([project site](https://spider2-sql.github.io/), [GitHub xlang-ai/Spider2](https://github.com/xlang-ai/Spider2), ICLR 2025 Oral) | 2024 | Benchmark (not a method): 600+ real enterprise-style tasks over BigQuery/Snowflake/dbt-style multi-step workflows, often requiring an *agent* with file/tool access, not single-shot SQL | — | — | Benchmark itself; shows single-shot "SQL in, SQL out" methods largely fail on realistic warehouse-scale tasks |
| OmniSQL, Alpha-SQL | 2024/25 | Found referenced in leaderboards/survey literature as further open-source generalist / search-based (Alpha-SQL uses MCTS-style search over SQL construction) systems in the same 2024–2025 wave | — | — | **Uncertainty flag**: could not independently verify precise benchmark numbers for these two in this pass; treat as "emerging, not yet load-bearing" for design decisions |

\* Some papers report Spider numbers on dev vs. test splits inconsistently; treat the Spider numbers above as approximate and cross-check against each paper if exact figures matter.

**Biggest gains vs. diminishing returns:**
- **Biggest gains, consistently**: (1) execution-feedback self-correction/repair loops (run → observe error/empty-result → retry), (2) multiple diverse candidates + a selection step, (3) good few-shot example retrieval (DAIL-SQL).
- **Matters less with strong reasoning models**: dedicated schema-linking modules (Distillery), heavy manual prompt engineering for "hard" sub-cases, and (for small schemas) exhaustive multi-agent decomposition — MAC-SQL/CHESS's multi-agent complexity pays off mainly on *large, messy, real* schemas (BIRD, Spider 2.0), less on small clean ones like ours.
- **Still hard regardless of model strength**: enterprise-scale, multi-dialect schemas (Spider 2.0) — not our situation, since our PoC has a handful of profiled CSVs.

---

## 2. Pipeline vs. Agentic Architectures

| Dimension | Fixed multi-stage pipeline | ReAct/tool-calling agent |
|---|---|---|
| Reliability | High — deterministic stage order, easy to unit-test each stage, easy to add hard guardrails (e.g., SELECT-only) | Variable — LLM decides control flow; can recover from the unexpected but can also loop, hallucinate tool args, or stop too early |
| Latency | Low — typically 1–3 LLM calls | Higher — each "thought → tool call → observation" round-trip is a full LLM call; 5–15+ calls common on hard questions |
| Cost | Predictable, lower | Higher, variable; harder to budget per-query |
| Flexibility / unknown schema | Lower — must anticipate stages | Higher — can `describe_table`, explore unfamiliar structure, ask follow-ups |
| Auditability | Easy to trace/replay a fixed DAG | Harder; requires full tool-call tracing (OpenAI Agents SDK / LangChain provide this) |
| Typical use | Production dashboards, latency-sensitive, compliance-heavy environments | Open-ended "chat with your warehouse," unfamiliar/huge schemas, exploratory analytics |

Representative implementations:
- **LangChain SQL Agent** ([docs.langchain.com/oss/python/langchain/sql-agent](https://docs.langchain.com/oss/python/langchain/sql-agent)) exposes `list_tables`, `get_schema`, `query`, `query_checker` as tools in a ReAct loop — canonical "agentic" reference implementation.
- **CHESS/MAC-SQL** are *pipelines with agent-like named stages* but a fixed DAG — not free-form ReAct. This is a useful middle ground: multiple specialized "agents," but orchestrated deterministically rather than by LLM-chosen control flow.
- **ReFoRCE / Spider-Agent** for Spider 2.0 are genuinely agentic (iterative column exploration, self-refinement loops with no fixed step count) — and this agency is specifically needed *because* Spider 2.0 schemas are too large/unfamiliar for a single-pass pipeline.
- **OpenAI Agents SDK** ([github.com/openai/openai-agents-python](https://github.com/openai/openai-agents-python), [platform.openai.com/docs/agents](https://platform.openai.com/docs/agents)) formalizes agents as LLM + instructions + tools + guardrails + tracing, with function tools (local Python) and hosted tools (e.g., Code Interpreter). This maps naturally onto a `run_sql`/`describe_table`/`get_profile` tool design for our agent, including built-in tracing for auditability — closing much of the "hard to trace" gap agentic designs otherwise have.

**Evidence on when each is preferred**: practitioner write-ups (e.g., sub-second LangChain+Groq pipelines) show fixed pipelines dominate when schema is known, latency matters, and governance is strict; agentic loops dominate when the schema is large/unfamiliar or the question is open-ended enough that no fixed DAG covers it. **Hybrid designs** (fixed pipeline + a bounded self-correction loop, or agent with a small, fixed toolset and a hard step cap) appear repeatedly in both the Spider 2.0 systems (ReFoRCE adds a repair loop onto a structured plan) and industry docs — this is the pattern most literature converges on for production use.

---

## 3. Industry Products and Open Source

| Product | Semantic layer | Ambiguity handling | Verification | Charts | Multi-turn |
|---|---|---|---|---|---|
| **Snowflake Cortex Analyst** ([docs.snowflake.com](https://docs.snowflake.com/en/user-guide/views-semantic/verified-query-repository)) | Semantic model YAML (logical tables/columns, synonyms, relationships) | Relies on semantic model specificity + synonyms to reduce ambiguity | **Verified Query Repository (VQR)**: curated question→SQL pairs in YAML, used as ground truth and for model evaluation/optimization | Via downstream BI | Session-based | 
| **Databricks AI/BI Genie** ([docs.databricks.com/aws/en/genie-agents/tune-quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality), [best-practices](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/best-practices)) | "Trusted assets": example SQL queries, Unity Catalog functions, knowledge-store snippets (synonyms/joins/metrics), plain-text instructions (last resort) | Explicit guidance: *prioritize SQL examples over text instructions*; shows generated SQL for human audit | Always shows generated SQL; curated benchmark question/expected-SQL sets for iterative QA | Native in Genie UI | Space-scoped conversational context |
| **ThoughtSpot Spotter** | Deep semantic model ("search tokens" → SQL) | Search-first UI constrains input to modeled concepts | Users can inspect parsed tokens + generated SQL | Auto interactive charts/tables | Strong — search-driven drill paths |
| **Power BI Copilot** | Power BI/Fabric dataset (measures/relationships), DAX | Relies on dataset's existing measure definitions | Partial — DAX is often opaque/nested | Native visual + report-page generation | Chat pane, Microsoft 365 integrated |
| **Looker / Google Conversational Analytics API (Gemini)** | LookML semantic model | Strong, inherited field-level definitions and row/column security | Logic generally reviewable with LookML lineage | Via Looker Studio | Multi-turn dialogue explicitly supported |
| **AWS Q in QuickSight** | Weaker/newer semantic layer (mostly RLS-based) | Less mature than LookML/ThoughtSpot | Improving query visibility | Native dashboard integration | Contextual Q&A |
| **Vanna.ai** ([GitHub](https://github.com/vanna-ai/vanna) — reported as archived as of ~March 2026) | None built-in; RAG over your own example Q/SQL pairs | None built-in — developer must add | None built-in | None built-in | Minimal |
| **Wren AI** ([getwren.ai](https://www.getwren.ai/)) | **MDL** (Modeling Definition Language): version-controlled YAML for models/relationships/calculated fields — closest open-source analog to our profiling+semantic-model approach | Semantic model reduces but doesn't eliminate ambiguity; governance features still maturing | Semantic model as source of truth; join-path governance in active development | Built-in GenBI dashboards | Supported |
| **Dataherald** | Schema-aware NL2SQL | — | — | — | — | Reported largely inactive post-2024 |
| **PandasAI** | None (pandas dataframe operations, not SQL) | — | — | Code-generated plots | Minimal |
| **Julius AI / DataLine / ChatGPT Advanced Data Analysis (Code Interpreter)** | None — ad hoc, per-session inference from uploaded file headers/sample rows | LLM infers meaning from raw data each session (no persistent semantic model) | Shows generated Python/pandas code for inspection | Matplotlib/plotly code-generated charts | Session-based chat history |

**Key lesson across all vendors**: the products considered most trustworthy (Cortex Analyst, Genie, Spotter, Looker) all pair a **durable, human-curated semantic layer** with a **growing cache of verified question→SQL examples**, and make the generated SQL visible for audit. Tools without a semantic layer (Vanna, PandasAI, ChatGPT Code Interpreter) are flexible but have no defense against metric drift or double-counting and depend entirely on per-session LLM inference quality. **Our PoC's profiling step (grain, FK cardinality, validated metric SQL) is structurally equivalent to Cortex Analyst's semantic-model YAML / Wren AI's MDL** — we should extend it with a small verified-query cache, which is the one component vendors treat as most important and we currently lack.

---

## 4. SQL vs. Python/Pandas Code Generation

| Benchmark | Year | Focus | Reported result |
|---|---|---|---|
| **DS-1000** ([paper](https://arxiv.org/abs/2211.11501), 2022) | Function-level data-science code completion (not end-to-end agentic) | Widely used but considered insufficient for agent evaluation | — |
| **InfiAgent-DABench** ([arXiv:2401.05507](https://arxiv.org/abs/2401.05507), [GitHub](https://github.com/InfiAgent/InfiAgent)) | 2024 | 603 closed-form questions over 124 real CSVs; agents must reason (ReAct), write/execute Python, self-debug | DAAgent baseline surpasses GPT-3.5 on several metrics; closed-form scoring enables repeatable eval |
| **DA-Code** ([ACL 2024](https://aclanthology.org/2024.emnlp-main.748.pdf), [site](https://da-code-bench.github.io/)) | 2024 | Agentic data-science code generation on real, complex datasets | Leading LLMs ~30.5% accuracy — large headroom remains |
| **DSBench** ([ICLR 2025](https://github.com/LiqiangJing/DSBench)) | 2024 | Realistic data analysis *and* modeling tasks from Kaggle/ModelOff | Current agents (ChatGPT etc.) still substantially below human baselines (exact numbers not independently confirmed here) |
| **DABstep** ([HF blog](https://huggingface.co/blog/dabstep), Adyen/HF) | 2024/25 | 450+ multi-step, cross-document reasoning tasks | SOTA agents ~16% accuracy — reveals large gap for compounding multi-step analysis |

**Tradeoffs**: SQL is better for (a) set-based aggregation correctness (joins/grain are explicit), (b) leveraging DuckDB's performance on large tabular data, (c) auditability (a SQL string is easy for a human/validator to check against a semantic model). Python/pandas is better for (a) ad hoc data cleaning/reshaping that doesn't map cleanly to SQL, (b) statistical/ML-style analysis, (c) flexible plotting. The benchmark evidence (DA-Code, DSBench, DABstep all show agents struggling most on *multi-step, cross-file reasoning* and data wrangling, not on single aggregations) supports a **hybrid**: use validated SQL (via our semantic model) for the core cross-table aggregation/filtering, and a constrained, sandboxed Python/pandas step only for presentation-layer cleaning or derived-stat computation on the already-reduced result set — never for the main join logic, where SQL + our semantic model's verified FK relationships is strictly safer against fan-out errors.

---

## 5. Multi-Turn Conversations

- **SParC** ([GitHub taoyds/sparc](https://github.com/taoyds/sparc)) extends Spider to multi-turn, context-dependent interactions requiring the system to carry state across turns; remains hard due to compositional generalization across turns.
- **CoSQL** ([arXiv:1909.05378](https://arxiv.org/abs/1909.05378)) — 30k+ turns / 3k dialogues across 200 DBs, simulating a DB-expert/user dialogue; explicitly frames **SQL-grounded dialogue state tracking** as a core open challenge. T5-3B + multi-task + reranking improved exact match by ~1.0% and execution match by ~3.4% over prior baselines — modest gains, underscoring that this remains unsolved even with strong seq2seq models.
- **Self-play data augmentation** ([ACL Findings 2022](https://aclanthology.org/2022.findings-emnlp.411/)) synthesizes additional multi-turn interactions to improve generalization on both datasets — a training-time technique, less relevant to our in-context (no fine-tuning) approach.
- **Follow-up handling strategies** in practice fall into three patterns: (1) **rewrite to standalone question** (resolve pronouns/ellipsis using history, then run the single-turn pipeline fresh — most robust, what we'd recommend), (2) **pass full history to the LLM** and let it infer context implicitly (simpler, but risks drift and higher token cost as conversation grows), (3) **edit the previous SQL directly** (e.g., add a `GROUP BY category` to the prior query) — fast and precise for narrow follow-up intents like "now break it down by X," but brittle for anything beyond minor edits. **Recommendation**: use rewrite-to-standalone as the default (works for "break down by category," "what about last quarter," etc.), with SQL-editing as a fast-path optimization only when the follow-up intent is clearly a narrow refinement of the prior query (same tables/metric, add a dimension or filter).

---

## 6. Visualization Generation

- **LIDA (Microsoft)** ([arXiv:2303.02927](https://arxiv.org/abs/2303.02927), [GitHub microsoft/lida](https://github.com/microsoft/lida)) — modular pipeline: Summarizer → Goal Explorer → VisGenerator → Infographer; grammar-agnostic (matplotlib/seaborn/Altair/D3); supports self-evaluation/repair of generated chart code.
- **Chat2VIS** ([arXiv:2302.02094](https://arxiv.org/abs/2302.02094)) — shows LLMs (GPT-3/Codex/ChatGPT) can go directly from free-form, even underspecified NL to visualization code, outperforming rule-based NLI-to-Vis approaches; evaluated against **nvBench**.
- **nvBench / nvBench 2.0** ([NeurIPS 2025](https://proceedings.neurips.cc/paper_files/paper/2025/hash/b5f985cdc1796defe83a6b2f84fe2ab6-Abstract-Datasets_and_Benchmarks_Track.html), [GitHub HKUSTDial/nvBench-2.0](https://github.com/HKUSTDial/nvBench-2.0)) — v2 focuses specifically on **ambiguity** in text-to-vis (one query → multiple valid charts), with 7,878 ambiguous queries → 24,076 visualizations across 780 tables/153 domains, and stepwise reasoning annotations for how ambiguity resolves to a specific chart.
- **VisEval** ([arXiv:2407.00981](https://arxiv.org/abs/2407.00981), [GitHub microsoft/VisEval](https://github.com/microsoft/VisEval)) — 2,524 queries / 146 DBs; evaluates **validity** (does code run and produce a chart), **legality** (does it match requested chart type/data), **readability** (visual clarity) — a good template for our own chart QA.
- **Data Formulator** (Microsoft) — related interactive NL-to-chart tool; no dedicated public benchmark found independently in this pass (flagged as uncertain).
- **Chart-type selection heuristics**: Mackinlay's 1986 APT work and Tableau's "Show Me" still function as a useful **rule-based sanity check** layered on top of LLM chart choice (e.g., time column → line chart; low-cardinality categorical + measure → bar; two measures → scatter) — benchmarks like VisEval implicitly reward LLM outputs that agree with this classic logic.
- **Spec formats**: Vega-Lite is the dominant structured spec format in academic tooling (declarative, easy to validate/repair programmatically); custom JSON (e.g., `{type, x, y, series}`) is common in product UIs for tighter control over a specific frontend charting library (e.g., Recharts in React). For a PoC with a fixed React frontend, a **small custom JSON chart spec validated against a schema** is simpler to implement and verify than full Vega-Lite, at the cost of expressive power.

---

## 7. Reliability & Safety

- **Ambiguity detection/clarification**: **AmbiQT** ([OpenReview](https://openreview.net/forum?id=7RsyEGRZthx), [GitHub testzer0/AmbiQT](https://github.com/testzer0/ambiqt)) — 3,000+ examples with two plausible SQLs per question; proposes "LogicalBeam" decoding to surface multiple interpretations instead of picking one silently. **AMBROSIA** ([arXiv:2406.19073](https://arxiv.org/abs/2406.19073), NeurIPS 2024 D&B) — categorizes scope/attachment/vagueness ambiguity even *with* schema provided; LLMs still struggle. **PRACTIQ** ([arXiv:2410.11076](https://arxiv.org/abs/2410.11076), Amazon Science) — conversational dataset combining ambiguous *and* unanswerable queries, with a clarification-dialogue flow (query → system clarification question → user answer → final SQL) — directly models the "ask one clarifying question" UX pattern we should adopt for genuinely ambiguous inputs. **AmbiSQL** ([GitHub](https://github.com/JustinzjDing/AmbiSQL)) is a concrete reference implementation: detect ambiguous spans → multiple-choice clarification → rewrite → generate.
- **Abstention/unanswerable**: **TrustSQL** and **EHRSQL** (healthcare-domain) are cited across this literature as motivating "the model must be able to say I can't answer this" rather than guessing — relevant for us when a question references a column/table that doesn't exist or asks something outside the semantic model's defined metrics.
- **Fan/chasm trap**: joining a table to two or more "many" tables (fan trap) or joining two "many-to-one" paths into a shared dimension (chasm trap) inflates SUM/COUNT results through row duplication ([Coginiti glossary](https://coginiti.co/glossary/fan-chasm-traps/), [Snowflake engineering blog](https://www.snowflake.com/en/blog/engineering/why-we-need-semantic-views/)). **This is precisely why our profiling step's FK cardinality + fan-out warnings exist** — the mitigation is to aggregate each fact table to its correct grain *before* joining (or use `COUNT(DISTINCT ...)`), and a semantic layer should refuse/flag any generated SQL that joins two "many" relationships without pre-aggregation.
- **SQL validation (SQLGlot)**: parse generated SQL into an AST (not regex/blacklist matching, which is bypassable), verify the root node is `Select` only, reject DDL/DML, block dangerous functions, and layer this with DB-level read-only roles as defense-in-depth (e.g., `PRAGMA query_only=ON` for SQLite/DuckDB-style enforcement). This two-layer approach (AST check + DB permission) is the pattern recommended across multiple practitioner write-ups referenced above.
- **Prompt injection via data values**: because cell values (not just the user's question) can reach the LLM's context (e.g., via sample rows in prompts or via executed-result summarization), the same AST-based, execute-with-read-only-role discipline protects against SQL-injection-like attacks originating from data content, not just from the user's typed question.
- **Hallucinated numbers / grounding**: because the final answer is narrated prose, a critical check is to **ground every number in the narration against the actual executed query result** (e.g., regex/structured extraction of the result set, compare numeric tokens in the narration against it) rather than trust the LLM to restate numbers correctly from its own context.
- **Relative date resolution**: "last quarter," "this year so far" need deterministic resolution against the current system date *before* SQL generation (ideally as a pre-processing step that substitutes explicit date ranges into the prompt), not left to the LLM to compute inline.

---

## 8. Evaluation Methods

| Metric/Method | Defined by | What it measures |
|---|---|---|
| **Execution Accuracy (EX)** | Spider/BIRD | Predicted SQL executes and returns identical result set to gold SQL |
| **Exact-set-match / Test-suite accuracy** | Spider | Predicted SQL is semantically equivalent across many synthesized DB instances, not just one |
| **VES / R-VES (Valid Efficiency Score / Reward-based VES)** | BIRD ([bird-bench/mini_dev](https://github.com/bird-bench/mini_dev)) | Rewards SQL that is both correct *and* efficient (execution time/resource use) |
| **Soft-F1** | BIRD | Lenient semantic-similarity scoring tolerant of column ordering/minor formatting differences |
| **Dr.Spider** ([OpenReview](https://openreview.net/forum?id=Wc5bmZZU9cy), [arXiv:2301.08881](https://ar5iv.labs.arxiv.org/html/2301.08881)) | Robustness diagnostic | 17 perturbation types (typos, paraphrase, schema/column reordering) on top of Spider; SOTA models drop up to ~14% overall, >50% on hardest perturbation types — a strong argument for robustness testing beyond clean gold-SQL sets |
| **LLM-as-judge** | Common practice, not a single paper | Used for narration/insight quality, chart appropriateness, and partial-credit grading where exact-match is too strict; not yet standardized the way EX is |
| **Human eval (vendor practice)** | Databricks Genie / Cortex Analyst docs | Curate representative NL questions + expected SQL, iterate and re-benchmark as semantic model/examples are tuned; track accuracy over releases |

**For our context**, since we already have gold-SQL eval sets (Jaffle Shop, Chinook, TPC-H), the recommended evaluation stack is:
1. **Execution accuracy** (and ideally a Soft-F1-style lenient variant) against the gold-SQL sets as the primary regression metric.
2. **Dr.Spider-style perturbation testing** (paraphrase the NL question, reorder columns in profiling output) to probe robustness, since our semantic-model approach should be *more* robust to this than raw schema-only prompting — worth explicitly testing that claim.
3. **LLM-as-judge grading of narration** (does the prose correctly and completely summarize the executed result, without unsupported claims) as a second axis, since EX alone says nothing about whether the final *answer text* is trustworthy.
4. **Large-data performance testing**: since backend is DuckDB, test query latency and correctness as row counts scale (e.g., TPC-H scale factors) — a dimension none of the text-to-SQL benchmarks above directly test, since most assume small SQLite databases.
5. **Edge-case suite**: ambiguous question, unanswerable question (references nonexistent column), fan-out-risk cross-table question, multi-turn follow-up, and malformed/garbage CSV input — inspired directly by AmbiQT/AMBROSIA/PRACTIQ/TrustSQL categories above.

---

## Design Options for Our Agent

### Option A — Structured pipeline: understand → plan+SQL → guard → execute → repair → chart → narrate
**Pros**: Matches the proven pattern from DIN-SQL/MAC-SQL/CHESS (fixed stages, each independently testable); lowest latency/cost (bounded number of LLM calls); easiest to add hard guardrails (SELECT-only, fan-out check) between stages; easiest to log/replay for debugging in a take-home timeframe; aligns with vendor pattern (semantic model + verified queries) we already partly have.
**Cons**: Less graceful on genuinely novel/unanticipated questions (e.g., if the user asks something the planner can't classify into a known stage); a fixed "repair" stage needs its own bounded retry logic to avoid being too rigid.

### Option B — Agentic tool-calling loop (`run_sql`, `describe_table`, `get_profile`, etc.)
**Pros**: Naturally handles exploration ("what tables do I have?"), ambiguous/underspecified questions, and multi-turn follow-ups without a brittle fixed DAG; matches ReFoRCE/Spider-Agent's demonstrated value *when schema is large/unfamiliar*; OpenAI Agents SDK gives built-in tracing/guardrails, mitigating the "hard to audit" downside.
**Cons**: Higher latency/cost (multiple round-trips per question), harder to guarantee it always charts/narrates/validates (must enforce via tool contracts and guardrails rather than a known DAG); for our small, already-profiled schemas, the "exploration" benefit of agency is partly redundant with information our profiling step already supplies.

### Option C — Hybrid: pipeline backbone with a bounded agentic core for SQL generation+repair
A structured outer flow (intent/plan → guarded execution → chart → narrate) with an **inner agentic loop only around SQL generation**: the LLM gets `run_sql` (read-only, auto-EXPLAIN/validate via SQLGlot) and `describe_table`/`get_profile` tools, with a hard cap (e.g., 3–4 tool calls) before falling back to a clarification question. This mirrors how ReFoRCE/CHESS actually work in practice (named "agents" inside an overall fixed pipeline, i.e., pipelines-of-agents rather than free-form ReAct) and is consistent with the hybrid pattern the literature converges on.
**Pros**: Gets agentic robustness (self-correction via execution feedback, ability to peek at profile/sample data if the plan step was wrong) without unbounded cost/latency; still produces a predictable artifact sequence (plan → SQL → chart → narration) for the rest of the pipeline and for logging/eval; aligns best with "Distillery"'s finding that explicit schema linking is unneeded for our schema sizes, while still using tool calls for genuinely useful dynamic lookups (sample values for a column, confirming a date range).
**Cons**: More implementation complexity than A; still needs careful guardrail/stop-condition design to avoid runaway loops.

### Recommendation
**Option C (bounded hybrid)**, for this context specifically: our schemas are small and already profiled (so heavy agentic *schema exploration* adds little — consistent with Distillery's argument), but reasoning-model-driven SQL generation benefits measurably from execution-feedback self-correction (MAC-SQL's Refiner, CHESS's Unit Tester, ReFoRCE's self-refinement all show this), and multi-turn/ambiguous questions benefit from at least minimal tool access (e.g., re-checking the semantic model or a sample of values) rather than a single committed guess. A fixed outer pipeline keeps the whole system auditable and fast for the common case, while the small inner loop (capped iterations, read-only `run_sql` + `describe_table`/`get_profile` tools) absorbs the genuine uncertainty that a single-shot generation step cannot.

---

## Prioritized Techniques to Adopt (and to Skip) for the PoC

**Adopt, roughly in priority order:**
1. **Execution-feedback repair loop** (run SQL → on error/empty/suspicious result, feed the error back to the LLM, retry with a cap of ~2–3 attempts) — highest ROI technique across nearly every paper reviewed.
2. **Verified-query cache/repository** seeded from our gold-SQL eval sets and example questions already in the semantic model (Cortex Analyst/Genie pattern) — reuse known-good SQL for recognized question patterns before generating fresh SQL.
3. **AST-based SQL validation (SQLGlot) + DB-level read-only enforcement** before any execution — non-negotiable safety baseline.
4. **Fan-out/chasm-trap guard**: before executing a join across two "many" relationships flagged by our FK cardinality metadata, require pre-aggregation or reject/flag the query for repair — directly exploits data we already compute.
5. **Rewrite-follow-up-to-standalone** for multi-turn ("break it down by category" → resolved into a full new question using conversation history) rather than raw history-passing.
6. **Deterministic relative-date resolution** pre-processing (resolve "last quarter" to explicit dates before the LLM sees the question).
7. **Narration-grounding check**: verify numbers quoted in the final prose actually appear in the executed result set.
8. **Lightweight clarification turn** for detected ambiguity (e.g., a term matches two synonyms in different tables, or a metric isn't in the semantic model) — modeled on PRACTIQ/AmbiSQL's ask-one-clarifying-question pattern, not full abstention research.
9. **Chart-type heuristic fallback** (Mackinlay/Show-Me-style rules) as a sanity check on/alternative to LLM-chosen chart type.
10. **Diverse-candidate + selection** (generate 2–3 SQL variants, pick via execution success + simplicity heuristic) — valuable if budget allows, but lower priority than #1–#4 for a PoC.

**Skip for the PoC (lower priority / not worth the complexity):**
- Dedicated schema-linking modules/embeddings — our profiling step plus a reasoning model make this largely redundant (Distillery's core finding), and our schemas are small enough to fit fully in context.
- Fine-tuned selector models (CHASE-SQL's pairwise LLM selector, trained rerankers) — too much ML infra for a take-home; use simple heuristics (execution success, row-count sanity, agreement across candidates) instead.
- Full free-form ReAct agent with unbounded tool-calling — cost/latency risk outweighs benefit at our schema scale; use the bounded hybrid instead.
- Full Vega-Lite spec generation — a custom, schema-validated JSON chart spec matched to our React frontend's charting library is simpler and sufficient.
- MCTS-style search (Alpha-SQL-style) or large multi-agent ensembles (MAC-SQL/CHESS's full 3–4 agent pipelines) — designed for large/messy schemas (BIRD) we don't have.
- Training-time self-play data augmentation (SParC/CoSQL style) — irrelevant without fine-tuning.

---

## References

1. DIN-SQL — https://arxiv.org/abs/2304.11015
2. DAIL-SQL — https://arxiv.org/abs/2308.15363
3. C3 (Zero-shot Text-to-SQL with ChatGPT) — https://arxiv.org/abs/2307.07306
4. MAC-SQL — https://arxiv.org/abs/2312.11242 ; https://github.com/wbbeyourself/MAC-SQL
5. CHESS — https://arxiv.org/abs/2405.16755 ; https://github.com/ShayanTalaei/CHESS
6. CHASE-SQL — https://arxiv.org/abs/2410.01943 (ICLR 2025)
7. MCS-SQL — https://aclanthology.org/2025.coling-main.24/
8. RSL-SQL — https://arxiv.org/abs/2411.00073
9. XiYan-SQL — https://arxiv.org/abs/2507.04701 ; preview https://arxiv.org/abs/2411.08599 ; https://github.com/alibaba/XiYan-SQL
10. Distillery / "The Death of Schema Linking?" — https://openreview.net/forum?id=fglyh5pa7d (NeurIPS 2024 TRL workshop)
11. ReFoRCE — https://arxiv.org/abs/2502.00675 ; https://github.com/Snowflake-Labs/ReFoRCE
12. Spider 2.0 — https://spider2-sql.github.io/ ; https://github.com/xlang-ai/Spider2 (ICLR 2025 Oral)
13. Extractive Schema Linking — https://arxiv.org/abs/2501.17174
14. Solid-SQL — https://aclanthology.org/2025.coling-main.654/
15. LangChain SQL Agent docs — https://docs.langchain.com/oss/python/langchain/sql-agent
16. OpenAI Agents SDK — https://github.com/openai/openai-agents-python ; https://platform.openai.com/docs/agents
17. Snowflake Cortex Analyst / Verified Query Repository — https://docs.snowflake.com/en/user-guide/views-semantic/verified-query-repository ; https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization ; https://docs.snowflake.cn/en/en/user-guide/snowflake-cortex/cortex-analyst-evaluations
18. Databricks AI/BI Genie — https://docs.databricks.com/aws/en/genie-agents/tune-quality ; https://learn.microsoft.com/en-us/azure/databricks/genie-agents/best-practices
19. Wren AI / MDL — https://www.getwren.ai/ ; https://www.getwren.ai/post/wren-ai-vs-vanna-the-enterprise-guide-to-choosing-a-text-to-sql-solution
20. Vanna.ai — https://github.com/vanna-ai/vanna
21. Snowflake "Why Do We Need Semantic Views?" (fan/chasm traps) — https://www.snowflake.com/en/blog/engineering/why-we-need-semantic-views/
22. Fan and Chasm Traps glossary — https://coginiti.co/glossary/fan-chasm-traps/
23. DA-Code — https://da-code-bench.github.io/ ; https://aclanthology.org/2024.emnlp-main.748.pdf
24. DSBench — https://github.com/LiqiangJing/DSBench (ICLR 2025)
25. InfiAgent-DABench — https://arxiv.org/abs/2401.05507 ; https://github.com/InfiAgent/InfiAgent
26. DABstep — https://huggingface.co/blog/dabstep
27. DS-1000 — https://arxiv.org/abs/2211.11501
28. SParC — https://github.com/taoyds/sparc
29. CoSQL — https://arxiv.org/abs/1909.05378 ; https://aclanthology.org/D19-1204/
30. Self-play augmentation for SParC/CoSQL — https://aclanthology.org/2022.findings-emnlp.411/
31. LIDA — https://arxiv.org/abs/2303.02927 ; https://github.com/microsoft/lida
32. Chat2VIS — https://arxiv.org/abs/2302.02094
33. nvBench 2.0 — https://proceedings.neurips.cc/paper_files/paper/2025/hash/b5f985cdc1796defe83a6b2f84fe2ab6-Abstract-Datasets_and_Benchmarks_Track.html ; https://github.com/HKUSTDial/nvBench-2.0
34. VisEval — https://arxiv.org/abs/2407.00981 ; https://github.com/microsoft/VisEval
35. AmbiQT — https://openreview.net/forum?id=7RsyEGRZthx ; https://github.com/testzer0/ambiqt
36. AMBROSIA — https://arxiv.org/abs/2406.19073 (NeurIPS 2024 D&B)
37. PRACTIQ — https://arxiv.org/abs/2410.11076 (Amazon Science)
38. AmbiSQL — https://github.com/JustinzjDing/AmbiSQL
39. BIRD benchmark / evaluation framework — https://github.com/bird-bench/mini_dev
40. Dr.Spider — https://arxiv.org/abs/2301.08881 ; https://openreview.net/forum?id=Wc5bmZZU9cy
41. SQLGlot — https://github.com/tobymao/sqlglot (project referenced via multiple security write-ups)

**Flagged uncertainties**: (a) exact benchmark figures for OmniSQL and Alpha-SQL were not independently confirmed in this pass and should be verified directly against their papers before citing numbers; (b) some vendor-comparison sources (pricing, "as of 2026" framing) come from third-party blog aggregators rather than primary vendor documentation and should be spot-checked against current official docs before being used in any customer-facing claim; (c) Data Formulator's formal benchmark results were not located independently.