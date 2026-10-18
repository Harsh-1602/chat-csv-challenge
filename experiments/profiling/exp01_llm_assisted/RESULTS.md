# Experiment 01: results

Three runs on the same code with no cache: `results/` (run 1), `results/repeat2/` and `results/repeat3/`. Each folder has the full profile JSON for every dataset, `metrics.json`, `SUMMARY.md`, and `llm_log.jsonl` with every prompt and response.

## Scores (mean of 3 runs; per-run values in brackets)

| Dataset | Strategy | Rel P | Rel R | Rel F1 | PK acc | Role acc | Units | Latency |
|---|---|---|---|---|---|---|---|---|
| jaffle_shop | baseline v1 | 0.00 | 0.00 | 0.00 | 3/3 | 1.00 | 0/1 | 0.1 s |
| jaffle_shop | **exp01** | 1.00 | 1.00 | **1.00** | 3/3 | 1.00 | **1/3 runs** | ~12 s |
| chinook | baseline v1 | 1.00 | 0.82 | 0.90 | 10/11 | 0.98 | n/a | 0.2 s |
| chinook | **exp01** | 0.97 [1, 0.917, 1] | 1.00 | **0.986** | 11/11 | 0.963 [0.922, 0.984, 0.984] | n/a | ~30 s |
| tpch | baseline v1 | 0.00 | 0.00 | 0.00 | 0/8 | 0.74 | n/a | 0.4 s |
| tpch | **exp01** | 1.00 | 1.00 | **1.00** | 8/8 | 0.994 [0.983, 1, 1] | n/a | ~18 s |

**Cost per run** (all 3 datasets, 30 tables): 28 LLM calls, about 61k input and 11.6k output tokens, about **$0.043** with gpt-4.1-mini.

## What worked

- **Keys:** 22/22 primary keys in every run. These include the composite keys `playlisttrack(PlaylistId, TrackId)`, `partsupp(ps_partkey, ps_suppkey)` and `lineitem(l_orderkey, l_linenumber)`, which v1 cannot find. The SQL fallback was never needed: every LLM-proposed key passed verification.
- **Relationships:** recall was 22/22 in every run. Links with different names (`user_id → id`, `order_id → id`), prefixed keys (all of TPC-H), the role-named FK (`SupportRepId → EmployeeId`) and the self-reference (`ReportsTo → EmployeeId`) were all found.
- **Containment traps:** the LLM rejected them. In Jaffle Shop, `raw_payments.order_id` is 100% contained in `raw_customers.id` too, and the right parent was picked. In TPC-H, `l_linenumber` (1–7) is 100% contained in every key, and was rejected every time. 10 of 12 Jaffle Shop candidates, 60 of 71 Chinook candidates and 24 of 33 TPC-H candidates were correctly rejected.
- **Semantic model:** every generated metric expression ran (22/22 in run 1). Caveats covered fan-out ("orders can have several payments"), the self-reference, and TPC-H revenue = `l_extendedprice * (1 - l_discount)`.

## Errors and analysis

1. **Unit detection is unstable (cents: 1/3 runs).** In run 1 `amount` was labelled `cents`, and P6 turned that into `SUM(amount) / 100.0` plus a caveat. In runs 2 and 3 it was labelled "currency units", so the Total Revenue metric is **100x too high**. This is the most damaging error type because it gives a wrong answer silently. The cents rule sits in a long P2 prompt among many other instructions, and the model only applies it some of the time.
2. **The verifier accepted a degenerate self-link (repeat 2).** The LLM put `track.TrackId → track.TrackId` in `missing_links`. Containment is trivially 1.0 and the parent is unique, so it was accepted. This is a verifier bug: a column must never link to itself, and a proposed link must not start from the child's own PK.
3. **Role confusion between identifier and descriptive.** Emails, first/last names, an employee postal code (run 1) and `o_clerk` were labelled `identifier` because they are unique, human-facing identifiers. This has a low impact on SQL, since these columns are still used for filtering, but it adds noise to P3 candidates, because identifier columns become FK candidates.
4. **P4 latency grows with the number of candidates.** Chinook has 71 candidates, mostly integer ids that are trivially contained in other integer keys. Its P4 call takes 17–20 s, which is 60% of the dataset's time, because the model writes a reason for every candidate.

## Next experiment (exp02) proposals

- **Fix the verifier.** Drop self-column links, and drop proposed links whose child is the child table's own single PK.
- **Make unit checks a separate step.** Run a dedicated LLM pass over only the measure columns across all tables, with min/median/max, examples and the dataset context. It answers "unit and scale" with explicit options (major unit / minor unit (cents) / fraction / percent / unknown). Alternatively, use a self-consistency vote.
- **Prune candidates before P4.** For each child column, send only the top-k parents (k = 3), ranked by name similarity, then containment, then parent coverage. Or have the LLM return only accepted and uncertain decisions; rejections are implicit.
- **Tighten the identifier definition.** State that natural-language attributes (names, emails, phones) are `text` even when unique.
- **Measure stability.** Report the mean and min over 3 runs for every metric.

## Model upgrade: gpt-5.6-terra, reasoning effort medium (2026-10-17)

The same `strategy.py` and prompts (`exp01-v1`) were run 3 times without the cache, using `--model gpt-5.6-terra --reasoning-effort medium`. Results are in `results/gpt-5.6-terra-medium/run{1,2,3}/`.

| Dataset | Rel F1 | PK acc | Role acc | Units | Latency (3 runs) |
|---|---|---|---|---|---|
| jaffle_shop | 1.00 ×3 | 3/3 ×3 | 1.00 ×3 | **1/1 ×3** | 13.6 / 15.0 / 15.1 s |
| chinook | **1.00 ×3** | 11/11 ×3 | **1.00 ×3** | n/a | 24.9 / 25.4 / 25.5 s |
| tpch | 1.00 ×3 | 8/8 ×3 | 1.00 ×3 | n/a | 22.7 / 22.7 / 25.9 s |

- **All four gpt-4.1-mini errors are gone in every run.**
  - Cents were detected 3/3 times, and P6 produced `SUM(amount) / 100.0` each time.
  - No self-link or any other false positive.
  - No links went to `needs_review`.
  - Emails and names are no longer labelled as identifiers.
  - Every P6 metric is valid SQL (8 / 10 / 10 metrics per dataset).
- **Latency:** Chinook P4 dropped from about 18 s to about 9 s. P6 is now the slowest phase at 7–11 s. TPC-H is a few seconds slower than on mini; the others are similar or faster.
- **Cost:** 28 calls and about 61k input / 12.2k output tokens per run, the same token volume as mini. At the published third-party price of $2 / $12 per 1M tokens, that is **about $0.27 per run** (about $0.09 per dataset), roughly 6x mini.
- **Decision:** `gpt-5.6-terra` with medium effort is the new default in `common/llm.py`. Model-specific fixes from the exp02 list are no longer urgent, but the verifier self-link guard is still worth adding because it costs nothing. The next experiment should focus on harder data (messy CSVs, renamed or abbreviated columns, larger schemas) rather than these three datasets, which are now saturated.
