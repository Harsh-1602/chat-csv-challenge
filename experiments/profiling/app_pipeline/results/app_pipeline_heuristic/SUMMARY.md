# app_pipeline_heuristic: metrics

| Dataset | Rel P | Rel R | Rel F1 | Accepted | Needs review | PK acc | Role acc | Units | Latency (s) | LLM calls | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| jaffle_shop | 1.0 | 1.0 | 1.0 | 2/2 | 0 | 3/3 | 1.0 | 0/1 | 0.3 | 0 | 0/0 |
| chinook | 1.0 | 0.818 | 0.9 | 9/11 | 0 | 11/11 | 0.984 | 0/0 | 1.2 | 0 | 0/0 |
| tpch | 1.0 | 1.0 | 1.0 | 9/9 | 0 | 8/8 | 0.741 | 0/0 | 1.74 | 0 | 0/0 |

## Errors

### jaffle_shop
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: none
- Unit errors: raw_payments.amount expected cents got None

### chinook
- False-positive links: none
- Missed links: customer.supportrepid->employee.employeeid, employee.reportsto->employee.employeeid
- Primary-key errors: none
- Role errors: employee.ReportsTo (identifier→measure)
- Unit errors: none

### tpch
- False-positive links: none
- Missed links: none
- Primary-key errors: none
- Role errors: customer.c_custkey (identifier→measure), customer.c_nationkey (identifier→measure), lineitem.l_orderkey (identifier→measure), lineitem.l_partkey (identifier→measure), lineitem.l_suppkey (identifier→measure), nation.n_nationkey (identifier→measure), nation.n_regionkey (identifier→measure), orders.o_orderkey (identifier→measure), orders.o_custkey (identifier→measure), part.p_partkey (identifier→measure), partsupp.ps_partkey (identifier→measure), partsupp.ps_suppkey (identifier→measure), region.r_regionkey (identifier→measure), supplier.s_suppkey (identifier→measure), supplier.s_nationkey (identifier→measure)
- Unit errors: none
