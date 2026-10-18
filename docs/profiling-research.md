# Data Profiling for a Chat-Based, Multi-Table CSV Analysis Agent: A Research-Grounded Design

## 1. Academic Taxonomy of Data Profiling

The foundational reference is **Abedjan, Golab & Naumann, "Profiling Relational Data: A Survey," VLDB Journal 24(4):557–581, 2015** (DOI: [10.1007/s00778-015-0389-y](https://doi.org/10.1007/s00778-015-0389-y); open PDF: [MIT DSpace](https://dspace.mit.edu/bitstream/handle/1721.1/106176/778_2015_Article_389.pdf;sequence=1)), extended into the monograph **Abedjan, Golab, Naumann & Papenbrock, *Data Profiling*, Synthesis Lectures on Data Management, 2018** ([publisher page](https://books.google.com/books/about/Data_Profiling.html?id=LEF7DwAAQBAJ)).

The survey organizes profiling along granularity:
- **Single-column ("content") profiling**: cardinalities (row count, distinct count, null %, uniqueness ratio), value distributions (min/max/quantiles/histograms), and patterns/data types (syntactic patterns, type/length inference, domain classification).
- **Multi-column / dependency profiling**: the three core dependency types:
  - **Unique Column Combination (UCC)**: a column set *X* such that no two rows agree on all of *X*; minimal if no proper subset is also unique — the basis of candidate/primary keys.
  - **Functional Dependency (FD)**: *X → Y* holds if every pair of rows agreeing on *X* also agrees on *Y*; a UCC is equivalent to the FD *X → (all other columns)*.
  - **Inclusion Dependency (IND)**: *R[X] ⊆ S[Y]* — every value combination appearing in *X* (table *R*) also appears in *Y* (table *S*), possibly the same table (self-reference). An IND becomes a candidate **foreign key** specifically when *Y* is (part of) a key of *S*.
- **Approximate/partial dependencies**: real/dirty data rarely satisfies dependencies 100%, so the field reports dependencies that hold on "most" tuples with an error/violation threshold (e.g., the g3-error measure for FDs) rather than demanding exact satisfaction — directly relevant since real CSVs have nulls and typos.

The search space for dependency discovery is exponential (2ⁿ column subsets), so the field's engineering focus — covered in depth in Topic 2 — is pruning this lattice efficiently while tolerating noisy, non-exact data.

## 2. Algorithms for Key and Foreign-Key Discovery

| Algorithm | Problem | Key technique | Scalability approach | False-positive / pruning heuristic |
|---|---|---|---|---|
| **HyUCC** (Papenbrock & Naumann, BTW 2017) | UCC | Hybrid row-sampling + lattice validation | Adaptive interleave of cheap/exact phases | Exact; shares partition structures with HyFD |
| **DUCC** (Heise et al., PVLDB 2013, [DOI](https://doi.org/10.14778/2732240.2732248)) | UCC | Lattice-as-graph-coloring + random-walk pruning | Multi-core + distributed; up to 631× faster than GORDIAN | Random-walk lattice pruning |
| **SPIDER** (Bauckmann, Leser, Naumann, ICDE 2007, [PDF](https://hpi.de/fileadmin/user_upload/fachgebiete/naumann/publications/ICDE07.pdf)) | Unary IND | Sort each column, parallel cursor merge | Disk-based external sort | Exact sort-merge containment test |
| **BINDER** (Papenbrock et al., PVLDB 2015, [PDF](https://www.vldb.org/pvldb/vol8/p774-papenbrock.pdf)) | Unary + n-ary IND | Divide-and-conquer bucket/sort-merge | Scales to data ≫ RAM; up to 26× faster than SPIDER, 2500× than MIND | Apriori-style: n-ary IND tested only if all (n−1)-ary projections hold |
| **MIND** (De Marchi, Lopes, Petit, EDBT 2002 / JIIS 2009, [PDF](https://link.springer.com/content/pdf/10.1007/s10844-007-0048-x.pdf)) | Unary + n-ary IND | Level-wise Apriori candidate generation | In-memory; scales worse than BINDER | Anti-monotonicity (sub-IND satisfaction) |
| **FAIDA** (Kruse et al., BTW 2017, [PDF](https://hpi.de/fileadmin/user_upload/fachgebiete/naumann/publications/PDFs/2017_kruse_fast.pdf)) | Approximate IND | Probabilistic sketches instead of exact sorted sets | Sub-linear memory, large speedup | Bounded, empirically negligible false-positive rate |
| **Rostin et al. ML-FK** (WebDB 2009, [PDF](http://webdb09.cse.buffalo.edu/papers/Paper30/rostin_et_al_final.pdf)) | FK from INDs | Binary classifier (best: J48) over SQL-computable features | Post-filter over IND candidate set | Learned boundary over inclusion, cardinality ratio, value overlap, name similarity |
| **Zhang et al. Multi-Column FK** (VLDB 2010, [PDF](https://www.vldb.org/pvldb/vol3/p805-zhang.pdf)) | FK incl. composite | "Randomness" rule approximated via quantile-based Earth Mover's Distance | Two-pass, ~linear time (vs cubic exact EMD) | Statistical randomness/structure test, naturally composite-aware |
| **HoPF** (Jiang & Naumann, 2020, [DOI](https://doi.org/10.1007/s10844-019-00562-z)) | PK+FK, holistic | Joint scoring over *all* UCCs/INDs in the whole schema | Schema-wide pruning over UCC×IND space | PK chosen by whole-schema competition before FK derivation — avoids naive PK↔PK matches between unrelated auto-increment integer columns |
| **Metanome** ([HPI project](https://hpi.de/naumann/projects/data-profiling-and-analytics/metanome-data-profiling.html)) | Framework | Pluggable benchmarking platform | — | Hosts all the above for comparison |

The **false-positive problem central to your use case** is explicitly the subject of Rostin et al., Zhang et al., and HoPF. Rostin et al. show that raw INDs are dominated by "accidental" containments (e.g., two boolean columns), and that a **trained classifier over {inclusion, cardinality ratio, value-overlap, name similarity}** beats any single hand threshold, reporting near-100% F-measure with a J48 decision tree. Zhang et al. generalize this with an unsupervised **randomness/entropy test** (approximated via EMD) that naturally extends to **composite FKs** like TPC-H's multi-part keys. HoPF's key insight for your Chinook/TPC-H scenario is the explicit acknowledgment that **two unrelated auto-increment surrogate ID columns can trivially satisfy an IND in both directions** merely by being dense sequential integers — exactly the `CustomerId`/`EmployeeId`/`InvoiceId` situation — and it solves this not with a numeric threshold but by scoring PK candidacy holistically across the *whole schema* before deriving FKs only against the winning PK set, reporting ~88% PK recall / ~91% FK recall on its benchmarks.

## 3. Open-Source Profiling Tools: What They Compute

| Tool | Column stats | Semantic type detection | Cross-table FK detection | Constraint suggestion |
|---|---|---|---|---|
| [ydata-profiling](https://github.com/ydataai/ydata-profiling) | Full EDA (types via `visions`, nulls, distinct, min/max/mean/std/histograms, correlations, duplicates) | Regex-pattern types only (URL/Email/IP) | **No** | Via GE integration (`to_expectation_suite()`) |
| [DataProfiler (Capital One)](https://github.com/capitalone/DataProfiler) | Richest: adds Gini impurity, chi² matrix, duplicate-row count, confidence intervals | **Yes** — char-level CNN `DataLabeler` ([source](https://github.com/capitalone/DataProfiler/blob/main/dataprofiler/labelers/character_level_cnn_model.py)) labels ADDRESS/SSN/EMAIL/PHONE/PERSON/etc. | No | Profile-diffing for drift, not rule suggestion |
| [Great Expectations](https://docs.greatexpectations.io/) | Data Assistants infer null%, uniqueness, min/max ranges | Regex-only (user-selected) | No | **Yes** — core feature |
| [whylogs](https://github.com/whylabs/whylogs) | Sketch-based (HLL distinct, KLL quantiles), mergeable/streaming | No | No | `SummaryConstraint`/`ValueConstraint` from profile |
| [Deequ/PyDeequ](https://github.com/awslabs/deequ) ([VLDB 2018 paper](https://www.vldb.org/pvldb/vol11/p1781-schelter.pdf)) | Completeness, distinctness, histograms at Spark scale | No | No | **`ConstraintSuggestionRunner`** — core feature |
| [Soda](https://docs.soda.io/data-observability/profiling.md) | Distinct/null/min/max/histogram/frequent values | No | Declared cross-checks only (user names both tables) | No |
| [sweetviz](https://github.com/fbdesignpro/sweetviz) | Stats + association/correlation report | No | No | No |
| **DuckDB `SUMMARIZE`/`sniff_csv()`** ([docs](https://duckdb.org/docs/current/sql/statements/summarize)) | min/max/approx_unique/avg/std/quartiles/null% (approximate, Hyper-engine) | No | No | No |
| [csvkit `csvstat`](https://csvkit.readthedocs.io/en/latest/scripts/csvstat.html) | type, null, unique, min/max/mean, frequent values | No | No | No |
| **[Frictionless Table Schema](https://specs.frictionlessdata.io/table-schema/)** | Structural type inference | No | **Declarative only** — explicit `foreignKeys` array with `fields`/`reference.resource`/`reference.fields`, supports composite keys and self-reference (`resource: ""`) | Via `constraints` property |
| [Apache Griffin](https://griffin.apache.org/docs/quickstart.html) | count/min/max/sum/approx-distinct via DSL | No | Manual join-condition reconciliation only | No |
| [OpenMetadata](https://docs.open-metadata.org/v2.0.x/how-to-guides/data-quality-observability/profiler) | Full column stats + histograms | **Yes** — hybrid: regex column-name scanner + Presidio/spaCy NER on sample values | No (separate Lineage feature, pipeline-derived) | User-authored test cases |
| DataHub | Reuses GE profiler internals | No (separate glossary/classification) | No (Lineage is pipeline-derived, not value-based) | Ingests GE/dbt tests |

**The decisive finding**: across all 13 tools surveyed, **true automatic cross-table FK/relationship *discovery* from data content is absent.** Frictionless is the only one with a first-class way to *represent* a relationship, but it must be authored, not inferred. This confirms the ingestion step must build custom FK-candidate inference — exactly what Topic 2's algorithms target.

## 4. Semantic Type Detection

| System | Method | Reported accuracy |
|---|---|---|
| **Sherlock** (Hulsebos et al., KDD 2019, [arXiv:1905.10688](https://arxiv.org/abs/1905.10688)) | 1,588 features (char-dist, word2vec, doc2vec, stats) → multi-input DNN over 78 DBpedia-derived types from VizNet | Support-weighted F1 = 0.89 |
| **Sato** (Zhang et al., VLDB 2020, [arXiv:1911.06311](https://arxiv.org/abs/1911.06311)) | Sherlock + table-level LDA topic context + CRF over column sequence | F1 = 0.925 (weighted), 0.735 (macro) |
| **DoDuo** (Suhara et al., SIGMOD 2022, [arXiv:2104.01785](https://arxiv.org/abs/2104.01785)) | Whole table serialized into BERT; multi-task column-type + column-relation prediction | Outperforms Sherlock/Sato/TURL by up to 4.0% (type) / 11.9% (relation) F1 |
| **Pythagoras** ([EDBT 2024](https://github.com/DHBWMosbachWI/pythagoras)) | GNN over graph-structured table representation, targets **numerical** columns specifically (age/price/lat vs generic float) | Up to +22% F1 vs prior SOTA on numeric typing |
| **ArcheType** (Feuer et al., VLDB 2024, [arXiv:2310.18208](https://arxiv.org/abs/2310.18208)) | Zero-shot LLM prompting with context sampling + label remapping | SOTA zero-shot on SOTAB; combined with DoDuo surpasses fine-tuned SOTA |
| **Korini & Bizer** ("Column Type Annotation using ChatGPT," [arXiv:2306.00745](https://arxiv.org/abs/2306.00745)) | Two-step: (1) ask LLM for table's entity class, (2) narrow candidate-type vocabulary for per-column annotation | F1 > 85% zero/one-shot, competitive with RoBERTa fine-tuned on 356 examples |

Simpler regex/dictionary approaches remain the first line of defense in practice: ydata-profiling's `visions` typeset uses regex for URL/Email/IPAddress; OpenMetadata layers a **column-name regex scanner** before a heavier Presidio/spaCy NER pass on sample values ([docs](https://docs.open-metadata.org/v2.0.x/how-to-guides/data-quality-observability/profiler/auto-pii-tagging)); Great Expectations ships standard email/UUID/ZIP regex Expectations. The consistent pattern across production systems is **cheap regex/dictionary first → statistical/trained classifier → LLM prompt as a fallback/refiner**, which Korini & Bizer show can match fine-tuned models with zero training data when given sample values plus a constrained type ontology — directly applicable since your pipeline already has an LLM in the loop.

## 5. How LLM Text-to-SQL Systems Represent Schema & Profiling Metadata

| System | Schema format | Sample values? | PK/FK graph? | Col descriptions? | Measures/dimensions/grain? | Accuracy evidence |
|---|---|---|---|---|---|---|
| **Shkapenyuk et al.** (AT&T, [arXiv:2505.19988](https://arxiv.org/abs/2505.19988)) | Profiling + catalog + LLM-generated descriptions | Yes | Yes (candidate PK/FK from profiling) | Yes (LLM-generated) | — | #1 on BIRD leaderboard, three periods; profiling-metadata-alone beats human hints-alone |
| **CHESS** ([arXiv:2405.16755](https://arxiv.org/abs/2405.16755)) | Pruned schema + LSH/embedding value retrieval | Yes (via LSH index over all distinct values) | Via Schema Selector | — | — | 71.10% BIRD test; pruning gives ~2pp gain + 5× token reduction vs. unpruned schema |
| **CodeS** ([arXiv:2402.16347](https://arxiv.org/abs/2402.16347)) | Filtered schema + BM25/LCS value retrieval, comments on ambiguous names | Yes | — | Yes | — | New SOTA vs. DIN-SQL+GPT-4 (~56% BIRD) |
| **DIN-SQL** ([arXiv:2304.11015](https://arxiv.org/abs/2304.11015)) | Explicit schema-linking prompt enumerating FKs, CoT binding | — | Yes | — | — | NeurIPS 2023 |
| **DAIL-SQL** ([arXiv:2308.15363](https://arxiv.org/abs/2308.15363)) | CREATE TABLE DDL style | Yes | Yes (FK in DDL) | — | — | Finds DDL representation beats NL schema descriptions |
| **M-Schema** (XiYan-SQL) | Semi-structured serialization with types/PK/FK/sample values | Yes | Yes | Yes | — | Used in Alibaba's XiYan-SQL stack |
| **Snowflake Cortex Analyst** semantic views ([docs](https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec)) | YAML: tables, dimensions, time_dimensions, facts, metrics, relationships | Yes (`sample_values` field explicitly "improves search quality") | Yes (`primary_key`, `relationships`, cardinality auto-inferred) | Yes | Yes | Most exhaustive single-vendor spec found |
| **Databricks Genie** ([docs](https://docs.databricks.com/aws/en/genie-agents/concepts)) | Unity Catalog comments + sample values + curated SQL examples | Yes | Via Unity Catalog | Yes | Via "metric views" | Caps at 50 tables, recommends ≤5; "SQL expressions > examples > text instructions" |
| **Power BI autodetect** ([docs](https://learn.microsoft.com/en-us/power-bi/transform-model/desktop-create-and-manage-relationships)) | Deterministic (no LLM) | — | Name-match → uniqueness-based cardinality | — | — | Explicit "high confidence" gate (undisclosed numeric threshold); requires a unique-valued column on at least one side |
| **Wren AI MDL** | JSON/YAML models + relationships + calculated fields | — | Yes | Yes | Yes | Open-source semantic layer |
| **dbt Semantic Layer/MetricFlow** | Entities/measures/dimensions + declared grain | — | Via entities | Yes | **Yes — core concept** | — |

The strongest quantitative evidence that richer metadata helps: (1) Shkapenyuk et al.'s direct finding that **automated profiling metadata alone beats manually written hints alone**, and fusing sources beats either; (2) CHESS's **pruned-schema ablation (~2pp gain, 5× fewer tokens)** showing *relevant* metadata beats *more* metadata; (3) DAIL-SQL's finding that **DDL-style schema representation outperforms natural-language schema descriptions**; (4) Databricks' operational guidance to keep agents to ≤5–50 tables and pre-aggregate into "certified"/metric-view objects — a real-world echo of the schema-pruning principle. Snowflake's explicit instruction to populate `sample_values` ("add any value likely to be referenced in user questions") is a direct, vendor-stated best practice for the exact design question at hand.

## 6. CSV Ingestion Robustness

**Dialect sniffing**: **CleverCSV** ([van den Burg et al., DMKD 2019](https://doi.org/10.1007/s10618-019-00646-y), [arXiv:1811.11242](https://arxiv.org/abs/1811.11242)) reframes dialect detection as an optimization over a **data-consistency measure** combining row-length patterns and per-column type consistency, reporting **97% accuracy, a 21-point improvement over Python's `csv.Sniffer`** on 18,000+ real-world files spanning 39 distinct dialects observed in the wild.

**Encoding**: `chardet` layers BOM detection → null-byte heuristics → escape-sequence detection → markup charset declarations → statistical CJK probing, claiming 99.7% accuracy on its 3,138-file test set ([docs](https://chardet.readthedocs.io/en/latest/how-it-works.html)); `charset-normalizer` is a from-scratch statistical alternative favored for speed on large files.

**Numbers/dates**: DuckDB's `auto_detect`/`sniff_csv()` ([docs](https://duckdb.org/docs/current/data/csv/auto_detection)) tries a priority-ordered candidate type list and a **priority-ordered date-format list (ISO 8601 first)**, disambiguating DD/MM vs MM/DD by scanning the sample for *any* row with day>12 to lock the format for the whole column — more automatic than pandas' single global `dayfirst` flag or `dateutil`'s per-call `dayfirst`/`yearfirst`/`fuzzy` parameters. DuckDB has **no locale-aware thousands/decimal separator handling**, so comma-thousands, European decimal commas, and accounting-style parenthesized negatives all fall through to `VARCHAR` unless pre-normalized.

**Null tokens**: pandas' default `na_values` list is the de facto standard: `['-1.#IND','1.#QNAN','1.#IND','-1.#QNAN','#N/A N/A','#N/A','N/A','n/a','NA','<NA>','#NA','NULL','null','NaN','-NaN','nan','-nan','None','']` ([docs](https://pandas.pydata.org/docs/user_guide/io.html)) — 18 tokens plus empty string, including legacy Windows float-error strings.

## 7. Fan-Out / Join Cardinality Awareness

Classical **fan traps** (two independent 1:many relationships radiating from one parent, causing ambiguous association) and **chasm traps** (a missing direct relationship causing row loss through partial-participation joins) are the textbook precursors ([Connolly & Begg course notes](https://bdavison.napier.ac.uk/db/Notes/Entities/problems/); [Coginiti glossary](https://coginiti.co/glossary/fan-chasm-traps/)). In SQL practice, a fan trap is the classic **double-counting bug**: joining one parent to two independent child tables and `SUM()`-ing a parent measure, which gets multiplied by the row-count of the *other* branch.

**Looker's symmetric aggregates** ([docs](https://docs.cloud.google.com/looker/docs/reference/param-explore-symmetric-aggregates)) fix this on by default: every joined view must declare a guaranteed-unique `primary_key`; Looker hashes it into a large numeric `hashed_pk` and rewrites `SUM(amount)` as **`SUM(DISTINCT hashed_pk + amount) − SUM(DISTINCT hashed_pk)`**, so duplicated rows from a fan-out join collapse under `DISTINCT` while genuinely distinct rows remain counted once.

**dbt's "grain"** concept ([guide](https://www.getdbt.com/blog/guide-to-data-grain)) is the complementary discipline: declare the level of row-uniqueness for a model and enforce it with `unique`/`not_null` dbt tests on the grain key — a failing uniqueness test is the automated tripwire that a table isn't actually at its assumed grain, the precondition for a fan trap to silently corrupt aggregates. Cube.dev declares explicit join relationship types (`one_to_one`, `one_to_many`, `many_to_many`) that govern how joins are planned, rather than a runtime arithmetic fix.

---

## Recommended Profiling Design

Given CSV-scale data (~100k rows), DuckDB, a few-seconds interactive budget, and an available LLM, the design should be a **layered pipeline**: cheap/deterministic SQL-native passes first, statistical dependency discovery second, LLM-assisted scoring/description last, with user confirmation only for ambiguous or high-impact decisions.

**Step 1 — Robust ingestion.** Use DuckDB's `read_csv(..., auto_detect=true)`/`sniff_csv()` for dialect+type sniffing (its cross-row date-format disambiguation, per Topic 6, handles the DD/MM ambiguity better than a single global flag); fall back to CleverCSV's consistency-measure sniffing only if DuckDB's parse looks inconsistent (e.g., highly variable column counts) or on ambiguous-delimiter files. Normalize locale-formatted numbers (thousands separators, decimal commas, parenthesized negatives) with a pre-pass, since DuckDB has no `thousands=`/`decimal=` equivalent. Treat pandas' 19-token default `na_values` list, plus domain tokens like `-`, `?`, `999`, `unknown`, as candidate nulls, confirming any domain-specific sentinel with the user.

**Step 2 — Column-level statistical profiling (DuckDB-native, seconds-scale).** Run `SUMMARIZE` per table for type/min/max/approx_unique/avg/std/quartiles/null% "for free" (Topic 3). On ~100k rows this is sub-second. Compute exact `COUNT(DISTINCT)` for columns where `approx_unique` is near row count (candidate keys), since HyperLogLog approximation error matters most exactly at the uniqueness boundary.

**Step 3 — Column role classification (id/measure/dimension/date/text/flag).** Combine structural signals (type, cardinality ratio, name pattern) with cheap regex/dictionary heuristics (Topic 4): `*_id`/`*key`/`id` suffix + high-cardinality integer → **id**; numeric with low distinct-to-row ratio and non-id name → **measure**; low-cardinality string/int (<~20–50 distinct or <5% of row count, following the qualitative "low cardinality" framing common to ydata-profiling/DataProfiler alerts) → **dimension**; date-typed or date-pattern-matching string → **date**; boolean-like 2-valued column → **flag**; else → **text**. Apply DataProfiler/OpenMetadata-style regex for emails, phones, currency, percent, country/ISO codes (Topic 4) as a cheap pre-filter, and use an LLM pass (Korini & Bizer two-step: table-topic inference then constrained-vocabulary column labeling) only for columns the regex/cardinality heuristics leave ambiguous — zero-shot LLM CTA reports >85% F1, comparable to supervised baselines, with no training cost.

**Step 4 — Key discovery (UCC).** Compute single-column uniqueness directly from Step 2 (`approx_unique ≈ row_count`, confirmed exactly). For composite keys (TPC-H's `(l_orderkey, l_linenumber)` style), test 2-column combinations only among columns already flagged as **id/dimension** with reasonably high individual cardinality — a scoped, HyUCC/DUCC-inspired lattice search bounded to depth 2–3 rather than the full powerset, since at 100k rows and a handful of plausible key columns this is tractable within a few seconds without needing a full DUCC/HyUCC implementation.

**Step 5 — Inclusion-dependency / FK candidate generation.** For every pair of **id-role** columns across tables (and within a table, for self-references like `ReportsTo`/`SupportRepId`), test value-set containment (`EXCEPT`/`NOT IN` in DuckDB) in both directions — this is a direct, SQL-native analogue of SPIDER's sort-based containment check, cheap at this data scale. Generate name-similarity scores (edit distance / token overlap between `o_custkey`↔`c_custkey`, `CustomerId`↔`Customer.Id`, `SupportRepId`↔`Employee.EmployeeId`) per Rostin et al.'s feature set.

**Step 6 — FK scoring and false-positive suppression.** Score each surviving IND candidate using the Rostin/Zhang/HoPF feature set: (a) **containment ratio** (near-100% strongly favors FK; literature doesn't give one universal numeric cutoff, so treat it as a continuous score rather than a hard threshold, per HoPF's finding that no single percentage was reported); (b) **cardinality asymmetry** — the referenced side must itself be a Step-4 key (necessary condition, directly mirroring "Y must be a key of S" in the IND→FK definition); (c) **name similarity**; (d) **type match**. Critically, apply **HoPF's holistic rule**: select PKs for each table first via whole-table UCC competition, then only score FK candidates against the *winning* PK set — this is exactly what prevents the classic auto-increment trap (e.g., `Invoices.InvoiceId` spuriously "including" `Customers.CustomerId` purely because both are dense sequential integers) from producing a false FK. Where two candidates tie (e.g., Chinook's `SupportRepId → Employee.EmployeeId` vs. the self-referencing `ReportsTo → Employee.EmployeeId`), prefer the name-similarity signal to disambiguate, and surface both to the user if ambiguous.

**Step 7 — Relationship cardinality tagging.** For every accepted FK, compute whether the referencing-side column is itself unique (1:1) or not (1:many) — directly analogous to Power BI's uniqueness-based cardinality inference — and store this on the relationship. This is the information needed later to warn against/guard fan-out double-counting (Topic 7) when the LLM composes a join across two 1:many relationships from a common parent.

**Step 8 — Data quality flags.** Record null %, type-casting failures, duplicate rows, and — specifically because of Step 7 — any table where a declared/inferred "grain" key is *not* actually unique (a dbt-style grain test), since that is the precondition for fan-trap double counting once the LLM starts joining.

**Step 9 — LLM-friendly semantic context.** Following Shkapenyuk et al.'s finding that profiling-derived metadata beats manual hints, and Snowflake/Databricks' explicit practice of including sample values, assemble per table: grain statement ("one row per `order_id`"), column list with {role, type, null%, distinct count, 3–5 representative sample values, LLM-generated one-line description}, and a schema-wide **relationship graph**: `table.column → table.column (cardinality: one-to-many, confidence: high/medium, evidence: 99.8% containment + name match)`. Use CHESS/CodeS's pruning lesson: don't dump every column's full stats into every prompt — include only the FK graph + lightweight per-column summary by default, expanding to full stats/sample values only for columns the LLM's current query plan touches.

**Step 10 — User confirmation UI.** Surface only the **uncertain** cases for human confirmation, mirroring Power BI's "don't create the relationship unless confidence is high" philosophy: (a) FK candidates below a confidence band (e.g., containment <99% or name-similarity low) rather than every accepted relationship; (b) ambiguous multi-candidate matches (e.g., two columns both plausibly referencing the same PK); (c) ambiguous date-format columns where no row in the sample disambiguates day-first vs. month-first; (d) any table whose inferred "grain" key fails a uniqueness check, since this directly risks silent double counting in downstream SQL.

---

## Appendix A — Agent pipeline overview

**A. Ingestion / profiling (once per upload):** A1 receive & store → A2 parse & load → A3 type inference & fixing → A4 column statistics → A5 column roles & semantic types → A6 key discovery → A7 relationship discovery → A8 data quality → A9 semantic layer (LLM descriptions, grain) → A10 user review → A11 persist schema context.

**B. Chat loop (per message):** B1 load conversation state → B2 intent routing → B3 follow-up rewriting → B4 schema linking → B5 structured plan → B6 SQL generation → B7 SQL validation (SQLGlot, read-only) → B8 sandboxed execution → B9 repair loop → B10 result checks → B11 presentation (scalar/table/chart) → B12 data-cleaning side path → B13 state & logging.

## Appendix B — Gap analysis vs current implementation (`backend/app/profiling.py`)

| Profiling step | Current | Recommended | Gap |
|---|---|---|---|
| Load & type sniffing | DuckDB auto-detect | + pre-pass for currency/%/thousands/null tokens | Partial |
| Column statistics | Rich (nulls, distinct, quartiles, IQR outliers, top values) | Same (optionally `SUMMARIZE`) | Done |
| Column role | Name regex → identifier, else by type | Name + type + cardinality + regex; LLM for ambiguous; measure vs dimension | Partial |
| Semantic types / units | None | Regex/dictionary, then LLM | Missing |
| Primary key | First unique column whose name looks like an id | Any unique non-null column or 2–3 column combination, chosen by scoring | Missing (prefixed + composite keys) |
| Relationships | Exact name match to parent PK, ≥50% containment | All id-like pairs incl. self-ref; score = containment + name similarity + type + PK on the parent side; exclude pk→pk | Weak (Jaffle Shop 0/2, TPC-H 0/9, Chinook 9/11) |
| Relationship cardinality | 1:1 vs many:1 | + fan-out risk flag | Partial |
| Table grain | None | "One row per …", taken from the primary key | Missing |
| Data quality | Nulls, duplicates, constant columns, outliers | + orphaned references, numbers stored as text, inconsistent categories, broken grain | Partial |
| LLM schema context | None | DDL + PK/FK + sample values + descriptions + relationship graph, pruned per question | Missing |
| User confirmation | None | Show only uncertain relationships, date formats and grain problems | Missing |

Baseline numbers are produced by `eval/build_expected.py` (see `eval/README.md`).
