# hybrid

Model gpt-5.6-terra (reasoning medium), run 2026-10-17T18:15:22.

- Execution accuracy (SQL questions): **35/35** — {'chinook': '14/14', 'jaffle_shop': '10/10', 'tpch': '11/11'}
- Clarify/refuse behaviour: **6/6**
- Chart type matches: 11/11
- Latency p50 5.9s, max 9.94s; 115 LLM calls, 314,744 prompt / 13,301 completion tokens

| id | type | pass | status | steps | latency | question |
|---|---|---|---|---|---|---|
| js-01 | scalar | ✅ | answered | 1 | 6.86s | How many customers do we have? |
| js-02 | chart | ✅ 📊 | answered | 1 | 5.7s | How many orders are there in each status? |
| js-03 | table | ✅ | answered | 1 | 7.37s | What is the total amount paid in dollars, by payment method? |
| js-04 | table | ✅ | answered | 1 | 8.02s | Who are the top 5 customers by total amount paid? |
| js-05 | chart | ✅ 📊 | answered | 1 | 7.59s | Show the number of orders per week. |
| js-06 | scalar | ✅ | answered | 1 | 7.37s | How many customers have never placed an order? |
| js-07 | scalar | ✅ | answered | 1 | 4.98s | What is the average order value in dollars? |
| js-08 | table | ✅ | answered | 1 | 7.29s | Which orders were paid with more than one payment method? |
| js-09 | scalar | ✅ | answered | 1 | 5.18s | What share of orders were returned or are pending return? |
| js-10 | chart | ✅ 📊 | answered | 1 | 6.56s | And how does that break down by month? |
| js-11 | clarification | ✅ | answered | 1 | 4.41s | What was our revenue? |
| js-12 | refusal | ✅ | refuse | 0 | 2.02s | What is the profit margin per product? |
| ch-01 | scalar | ✅ | answered | 1 | 4.87s | How many tracks are in the catalog? |
| ch-02 | chart | ✅ 📊 | answered | 1 | 6.71s | Which 10 countries generate the most revenue? |
| ch-03 | chart | ✅ 📊 | answered | 1 | 6.16s | Show monthly revenue for 2013. |
| ch-04 | chart | ✅ 📊 | answered | 1 | 7.83s | What are the top 5 genres by number of tracks sold? |
| ch-05 | table | ✅ | answered | 1 | 7.31s | Which sales support agent has the highest total sales? |
| ch-06 | table | ✅ | answered | 1 | 6.25s | Which 5 artists have the most albums? |
| ch-07 | table | ✅ | answered | 1 | 5.63s | What is the average track length in minutes for each media type? |
| ch-08 | table | ✅ | answered | 1 | 7.09s | Which genre brings in the most revenue in the USA? |
| ch-09 | table | ✅ | answered | 1 | 5.9s | What about Canada? |
| ch-10 | scalar | ✅ | answered | 1 | 5.05s | What is the total revenue from Rock tracks? |
| ch-11 | scalar | ✅ | answered | 1 | 5.01s | How many tracks have never been purchased? |
| ch-12 | table | ✅ | answered | 1 | 5.7s | List each employee with the name of their manager. |
| ch-13 | chart | ✅ 📊 | answered | 1 | 8.18s | What was the year-over-year revenue growth? |
| ch-14 | table | ✅ | answered | 1 | 5.32s | Which playlist has the most tracks? |
| ch-15 | clarification | ✅ | answered | 1 | 5.33s | Who is our best customer? |
| ch-16 | refusal | ✅ | refuse | 0 | 2.76s | How many times was each track streamed last month? |
| tp-01 | scalar | ✅ | answered | 1 | 5.04s | How many orders were placed in 1995? |
| tp-02 | chart | ✅ 📊 | answered | 1 | 7.1s | How many customers are in each market segment? |
| tp-03 | chart | ✅ 📊 | answered | 1 | 9.05s | Show total revenue per year. |
| tp-04 | chart | ✅ 📊 | answered | 1 | 6.46s | What is the revenue by region? |
| tp-05 | table | ✅ | answered | 1 | 9.94s | Which 5 nations had the most revenue from local suppliers in Asia during 1994? |
| tp-06 | table | ✅ | answered | 1 | 8.62s | Summarize quantity, price and average discount of line items shipped on or before 1998-09-02, grouped by return flag and line status. |
| tp-07 | table | ✅ | answered | 1 | 6.31s | Who are the top 10 customers by total order value? |
| tp-08 | scalar | ✅ | answered | 1 | 4.43s | What percentage of line items were received later than their commit date? |
| tp-09 | chart | ✅ 📊 | answered | 1 | 5.52s | Break that down by ship mode. |
| tp-10 | scalar | ✅ | answered | 1 | 5.13s | How many customers have never placed an order? |
| tp-11 | scalar | ✅ | answered | 1 | 4.43s | What is the average order total? |
| tp-12 | clarification | ✅ | clarify | 0 | 2.19s | Which suppliers are the most profitable? |
| tp-13 | refusal | ✅ | refuse | 0 | 1.87s | What was the customer satisfaction score per region? |
