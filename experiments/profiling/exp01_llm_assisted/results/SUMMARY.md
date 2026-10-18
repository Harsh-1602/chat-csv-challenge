# exp01_llm_assisted: metrics

| Dataset | Rel P | Rel R | Rel F1 | Accepted | Needs review | PK acc | Role acc | Units | Latency (s) | LLM calls | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| jaffle_shop | 1.0 | 1.0 | 1.0 | 2/2 | 0 | 3/3 | 1.0 | 1/1 | 12.31 | 5 | 6269/1525 |
| chinook | 1.0 | 1.0 | 1.0 | 11/11 | 0 | 11/11 | 0.922 | 0/0 | 28.63 | 13 | 29812/5915 |
| tpch | 1.0 | 1.0 | 1.0 | 9/9 | 0 | 8/8 | 0.983 | 0/0 | 17.29 | 10 | 24864/4305 |

Model `gpt-4.1-mini`: 28 calls (0 cached, 0 failed), 60,945 input and 11,745 output tokens, estimated $0.04317.

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
- Role errors: customer.Email (descriptive→identifier), employee.LastName (descriptive→identifier), employee.FirstName (descriptive→identifier), employee.PostalCode (descriptive→identifier), employee.Email (descriptive→identifier)
- Unit errors: none

### tpch
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: orders.o_clerk (descriptive→identifier)
- Unit errors: none
