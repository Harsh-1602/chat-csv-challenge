# exp01_llm_assisted: metrics

| Dataset | Rel P | Rel R | Rel F1 | Accepted | Needs review | PK acc | Role acc | Units | Latency (s) | LLM calls | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| jaffle_shop | 1.0 | 1.0 | 1.0 | 2/2 | 0 | 3/3 | 1.0 | 1/1 | 15.12 | 5 | 6322/1765 |
| chinook | 1.0 | 1.0 | 1.0 | 11/11 | 0 | 11/11 | 1.0 | 0/0 | 24.92 | 13 | 29871/5551 |
| tpch | 1.0 | 1.0 | 1.0 | 9/9 | 0 | 8/8 | 1.0 | 0/0 | 25.93 | 10 | 24895/5214 |

Model `gpt-5.6-terra` (reasoning effort medium): 28 calls (0 cached, 0 failed), 61,088 input and 12,530 output tokens, estimated cost $0.27254.

## Errors

### jaffle_shop
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: none
- Unit errors: none

### chinook
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: none
- Unit errors: none

### tpch
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: none
- Unit errors: none
