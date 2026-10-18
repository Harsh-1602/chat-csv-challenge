# baseline_v1: metrics

| Dataset | Rel P | Rel R | Rel F1 | Accepted | Needs review | PK acc | Role acc | Units | Latency (s) | LLM calls | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| jaffle_shop | 0.0 | 0.0 | 0.0 | 6/2 | 0 | 3/3 | 1.0 | 0/1 | 0.08 | 0 | 0/0 |
| chinook | 1.0 | 0.818 | 0.9 | 9/11 | 0 | 10/11 | 0.984 | 0/0 | 0.26 | 0 | 0/0 |
| tpch | 0.0 | 0.0 | 0.0 | 0/9 | 0 | 0/8 | 0.741 | 0/0 | 0.36 | 0 | 0/0 |

## Errors

### jaffle_shop
- False-positive links: raw_customers.id->raw_orders.id, raw_customers.id->raw_payments.id, raw_orders.id->raw_customers.id, raw_orders.id->raw_payments.id, raw_payments.id->raw_customers.id, raw_payments.id->raw_orders.id
- Missed links: raw_orders.user_id->raw_customers.id, raw_payments.order_id->raw_orders.id
- Primary-key errors: none
- Role errors: none
- Unit errors: raw_payments.amount expected cents got None

### chinook
- False-positive links: none
- Missed links: customer.supportrepid->employee.employeeid, employee.reportsto->employee.employeeid
- Primary-key errors: playlisttrack expected ['PlaylistId', 'TrackId'] got []
- Role errors: employee.ReportsTo (identifier→measure)
- Unit errors: none

### tpch
- False-positive links: none
- Missed links: customer.c_nationkey->nation.n_nationkey, lineitem.l_orderkey->orders.o_orderkey, lineitem.l_partkey->part.p_partkey, lineitem.l_suppkey->supplier.s_suppkey, nation.n_regionkey->region.r_regionkey, orders.o_custkey->customer.c_custkey, partsupp.ps_partkey->part.p_partkey, partsupp.ps_suppkey->supplier.s_suppkey, supplier.s_nationkey->nation.n_nationkey
- Primary-key errors: region expected ['r_regionkey'] got [], nation expected ['n_nationkey'] got [], supplier expected ['s_suppkey'] got [], customer expected ['c_custkey'] got [], part expected ['p_partkey'] got [], orders expected ['o_orderkey'] got [], partsupp expected ['ps_partkey', 'ps_suppkey'] got [], lineitem expected ['l_orderkey', 'l_linenumber'] got []
- Role errors: customer.c_custkey (identifier→measure), customer.c_nationkey (identifier→measure), lineitem.l_orderkey (identifier→measure), lineitem.l_partkey (identifier→measure), lineitem.l_suppkey (identifier→measure), nation.n_nationkey (identifier→measure), nation.n_regionkey (identifier→measure), orders.o_orderkey (identifier→measure), orders.o_custkey (identifier→measure), part.p_partkey (identifier→measure), partsupp.ps_partkey (identifier→measure), partsupp.ps_suppkey (identifier→measure), region.r_regionkey (identifier→measure), supplier.s_suppkey (identifier→measure), supplier.s_nationkey (identifier→measure)
- Unit errors: none
