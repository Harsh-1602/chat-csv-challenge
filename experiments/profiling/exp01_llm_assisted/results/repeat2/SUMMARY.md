# exp01_llm_assisted: metrics

| Dataset | Rel P | Rel R | Rel F1 | Accepted | Needs review | PK acc | Role acc | Units | Latency (s) | LLM calls | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| jaffle_shop | 1.0 | 1.0 | 1.0 | 2/2 | 0 | 3/3 | 1.0 | 0/1 | 10.88 | 5 | 6288/1503 |
| chinook | 0.917 | 1.0 | 0.957 | 12/11 | 0 | 11/11 | 0.984 | 0/0 | 31.96 | 13 | 29840/5820 |
| tpch | 1.0 | 1.0 | 1.0 | 9/9 | 0 | 8/8 | 1.0 | 0/0 | 19.43 | 10 | 24866/4335 |

Model `gpt-4.1-mini`: 28 calls (0 cached, 0 failed), 60,994 input and 11,658 output tokens, estimated $0.04305.

## Errors

### jaffle_shop
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: none
- Unit errors: raw_payments.amount expected cents got currency units

### chinook
- False-positive links: track.trackid->track.trackid
- Missed links: none
- Primary-key errors: none
- Role errors: customer.Email (descriptive→identifier)
- Unit errors: none

### tpch
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: none
- Unit errors: none
