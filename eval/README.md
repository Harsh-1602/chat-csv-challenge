# Evaluation datasets and text-to-SQL question sets

Open multi-table CSV datasets with known relationships. Each one has natural-language questions, gold SQL, and expected answers computed from that SQL. They test the app beyond the assignment's sample data.

## Datasets included

| Dataset | Domain | CSVs | Rows | Relationships | License | Source |
|---|---|---|---|---|---|---|
| `jaffle_shop` | Shop: customers, orders, payments | 3 | 312 | 2 | Apache-2.0 | [dbt-labs/jaffle_shop](https://github.com/dbt-labs/jaffle_shop/tree/main/seeds) |
| `chinook` | Music store: artists, albums, tracks, playlists, customers, employees, invoices | 11 | 15,607 | 11 | MIT | [lerocha/chinook-database](https://github.com/lerocha/chinook-database) |
| `tpch` | Wholesale supplier benchmark (TPC-H, scale factor 0.01) | 8 | 86,805 | 9 | TPC-H data generator | DuckDB `tpch` extension (`CALL dbgen(sf = 0.01)`) |

Why these three:
- **Jaffle Shop** is closest to the assignment's data. It has orders and payments, payment amounts stored in cents, multiple payments per order, and foreign keys that don't share the key's name.
- **Chinook** is the classic text-to-SQL teaching database. It has many-to-many bridges, a self-reference (`ReportsTo`), and a key with a different name (`SupportRepId → EmployeeId`).
- **TPC-H** is a standard benchmark. Its relationships span 5 tables, its column names use per-table prefixes (`c_custkey`, `o_custkey`), and several questions are natural-language versions of the official TPC-H queries (Q1 and Q5).

## Question sets

The questions are in `questions/<dataset>.json`: 41 in total, 35 of them with gold SQL. Each question has a `category` and an `expected_type`:

- `scalar`, `table`: a numeric or tabular answer.
- `chart`: the answer should be rendered as a chart. The `chart` field gives the expected type and axes.
- `clarification`: the question is ambiguous (for example "What was our revenue?"). The assistant should ask, or state its assumption.
- `refusal`: the data can't answer the question. The assistant must say so and not invent numbers.

Categories cover lookups, aggregation, joins, joins across 3–5 tables, time series, ranking, anti-joins, self-joins, window functions, unit conversion, multi-turn follow-ups (`context` lists the earlier turns), and **join fan-out traps**: questions where the obvious join double-counts.

| Trap | Correct | Typical wrong answer |
|---|---|---|
| Jaffle Shop average order value | 16.89 | 14.80 (averaging payment rows) |
| Chinook Rock revenue | 826.65 | 7,720.02 (summing `invoice.Total` after joining invoice lines) |
| TPC-H average order total | 141,826.46 | 176,905.63 (averaging after joining line items) |

The expected answers are in `expected/<dataset>.json` (machine-readable) and `expected/SUMMARY.md` (readable: question, SQL, result preview). The build script regenerates both; don't edit them by hand.

## Usage

Run from the repository root:

```powershell
# 1. Download the CSVs into eval/datasets/ (git-ignored, ~11 MB)
backend\.venv\Scripts\python.exe eval\fetch_datasets.py

# 2. Load them exactly like the app does, run every gold query, score relationship detection
backend\.venv\Scripts\python.exe eval\build_expected.py
```

To try a dataset in the UI, upload all the CSVs from one `eval/datasets/<name>/` folder.

## Relationship detection baseline

The build also checks the profiler's relationship detection against the ground truth in each question file:

| Dataset | Correct / expected | False positives | Cause |
|---|---|---|---|
| chinook | 9 / 11 | 0 | Misses `customer.SupportRepId → employee.EmployeeId` and `employee.ReportsTo → employee.EmployeeId` (different column names) |
| jaffle_shop | 0 / 2 | 6 | Every table's `id` matches every other `id`; misses `user_id → id` and `order_id → id` |
| tpch | 0 / 9 | 0 | Prefixed keys (`c_custkey`) are not recognized as identifiers, so no primary keys are found |

Same-name matching works for some naming conventions but not others. The planned fix:
- Match columns on name similarity (strip table prefixes, `<table>_id → id`).
- Compare value overlap and check the primary-key side for uniqueness.
- Never link a table's own primary key to another table's primary key.
- Let the user confirm or edit the detected relationships.

## Other open datasets (not downloaded)

| Dataset | What it is | License | Why not included |
|---|---|---|---|
| [BIRD mini-dev](https://github.com/bird-bench/mini_dev) | 500 text-to-SQL questions with gold SQL and "evidence" hints over 11 real databases | CC BY-SA 4.0 | Databases ship as a 764 MB zip; questions are on [Hugging Face](https://huggingface.co/datasets/birdsql/bird_mini_dev). Good next step for a larger benchmark. |
| [Spider 1.0](https://yale-lily.github.io/spider) | ~10k questions over 200 SQLite databases | CC BY-SA 4.0 | Mostly small schemas with few rows; tests SQL structure more than analysis. |
| [KaggleDBQA](https://github.com/Chia-Hsuan-Lee/KaggleDBQA) | Questions over real Kaggle databases | CC BY 4.0 | Similar value to BIRD; small. |
| [Olist Brazilian e-commerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) | 9 realistic e-commerce CSVs (~100k orders) | CC BY-NC-SA 4.0 | Requires a Kaggle login; non-commercial license. No gold questions. |
| Northwind / Sakila / AdventureWorks | Classic sample databases | Various | Chinook covers the same patterns. |

For the BIRD databases, export each SQLite file to CSVs with DuckDB's `sqlite` extension, the same way `fetch_chinook` does.
