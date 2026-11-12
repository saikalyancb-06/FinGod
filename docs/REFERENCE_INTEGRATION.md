# How the team's existing repos were used

All three were cloned read-only into `reference/` (git-ignored) and analysed. **None of them contains a trained model or a dataset**, so the blueprint's "connect the existing model" steps are met as follows: their logic is ported where useful, an optional bridge is provided for the external CredWise API, and new models were trained on clearly-labelled synthetic data.

## muleguard (React + Rust/WASM, rule and graph detection)

| MuleGuard feature | Where it lives now | Notes |
|---|---|---|
| Cycle detection, length 3–5 | `mule/features.py::_temporal_cycles` | Stricter: edges must also occur **in time order within 72h**, and only significant amounts count. |
| Fan-in/out smurfing (≥10 unique in 72h, amounts ≤ 0.5 × median) | `mule/patterns.py::smurfing` | Added as rule families `smurfing_fan_in/out`, alongside our 24h fan-in/out. |
| Layered shell chains (intermediates with degree 2–3, ≥3 hops) | `mule/patterns.py::shell_chains` | Ported DFS with pruning; stages are reported as stage1/2/3+. |
| High-volume merchant false-positive filter | `mule/patterns.py::high_volume_merchants` | Suppresses non-high alerts and notes it in the evidence. |
| Ring IDs and ring risk (base + 2×members) | `mule/patterns.py::build_rings` | Shown in the Overview rings table and linked from alerts. |
| Account suspicion score (40/35/25 + 5 per pattern) | `pattern_score` on alerts | Shown **next to** the ML score, never averaged with it. |
| JSON download (suspicious_accounts / fraud_rings / summary) | `GET /api/datasets/{id}/export.json` | Uses aliased identifiers. |
| Live WebSocket mode (raw CSV or `{row}`) | `frontend/src/institution/LiveFeed.jsx` + `POST /api/datasets/{id}/append` | Re-analyses incrementally and preserves analysts' notes and statuses. |
| Cytoscape graph, cose for ≤400 nodes / grid otherwise, zoom and fit controls | `frontend/src/institution/Graph.jsx` | Wheel zoom disabled so the page still scrolls. |

## mr-mule (Express + ws live CSV feed)

- Its row schema `transaction_id,sender_id,receiver_id,amount,timestamp` is accepted directly.
- To demo it: `cd reference/mr-mule && npm install && npm start` (port 3000). Then in MuleScope → Overview → Live transaction feed, choose **Connect** to `ws://localhost:3000`.
- If you'd rather not run Node, **Replay synthetic stream** feeds the same pipeline.

## cred-wise (React + Express + Groq; credit for thin-file borrowers)

| CredWise feature | Where it lives now | Notes |
|---|---|---|
| Cash-flow score 300–850 (savings, investment, spend ratio, discretionary) | `finlens/credit.py::cashflow_score` + Credit page gauge and what-if | Uses FinLens's stricter income definition (recurring income only). Labelled "not a bureau score". |
| External loan model API (`POST /api/loan/predict`, Kaggle loan-approval fields) | `POST /api/credit/lender-model`, enabled by setting `LOAN_API_BASE_URL` | Per the blueprint's model-validation rule, that schema's label is **historical approval**, not repayment. It is a caveated side signal and never changes the FinLens affordability status. Note that CredWise sends `loan_term` in months while the Kaggle dataset uses years; we keep the CredWise contract. |
| UPI PDF statement parser | `finlens/pdf_statement.py::_from_upi_app` | Plus table and bank-line strategies. |
| EMI heuristic `min(0.4×surplus, 0.2×income)` | Superseded | FinLens uses the reducing-balance EMI plus explicit affordability rules and stress tests. |
| GitHub-style spending heatmap | `frontend/src/finlens/Heatmap.jsx` | |
| Groq chatbot with the ledger as context | Ask FinLens | Safer: only computed facts are sent, and figures are verified. |

Not carried over: the Supabase auth and storage (the blueprint specifies separate stores; this uses SQLAlchemy with SQLite or PostgreSQL); the unversioned BERT checkpoint (not in the repo); and the browser-side Groq key (a security issue, since keys stay server-side here).
