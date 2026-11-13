# Requirements: FinLens & Money Mule Detection

This document lists what you need to run the prototype (**system requirements**) and what the prototype is required to do (**functional and non-functional requirements**). Each requirement is traced to the project blueprint (`FinLens_Project_Blueprint.pdf`, sections §03–§14) and shows its status in this build.

Status legend: ✅ implemented and tested · 🟡 implemented with a documented limitation · ⏭️ out of scope for the prototype (by blueprint design)

---

## 1. System requirements

### 1.1 Software

| Component | Required version | Used for | Notes |
|---|---|---|---|
| Windows 10/11, macOS or Linux | any recent | host | Developed and verified on Windows 11 |
| **Python** | **3.13** (3.11+ should work) | both backends, ML training, tests | Verified on 3.13.1 |
| **Node.js** | **≥ 18** (verified 22.20) | React frontend (Vite 5) | npm 10 |
| Python packages | see `requirements.txt` (pinned) | API, analytics, ML, PDF | `pip install -r requirements.txt` |
| Dev/test packages | see `requirements-dev.txt` | pytest | `pip install -r requirements-dev.txt` |
| Training packages | see `requirements-train.txt` | dataset download (Hugging Face Hub) + fast CSV/parquet (pyarrow) | `pip install -r requirements-train.txt` |
| Frontend packages | see `frontend/package.json` (pinned) | React 18, React Router 6, Recharts 2, Cytoscape 3, Vite 5 | `npm install` in `frontend/` |
| PostgreSQL | optional, 14+ | production-style storage | Default is SQLite (no install). Set `FINLENS_DATABASE_URL` / `MULE_DATABASE_URL` and add `psycopg2-binary` |
| Node (for mr-mule live feed) | optional, ≥ 18 | demo live transaction stream | `reference/mr-mule` → `npm install && npm start` |

### 1.2 Hardware (prototype scale)

| Resource | Minimum | Notes |
|---|---|---|
| CPU | 2 cores | Mule analysis of ~8,400 transactions / ~370 accounts takes about 2–3 s |
| RAM | 4 GB to run; **16 GB to retrain** | Mule training on 31.9M transactions peaks at roughly 8–10 GB |
| Disk | ~500 MB to run; **~10 GB to retrain** | Running includes `frontend/node_modules`. Retraining downloads ~6 GB of datasets plus a feature cache |
| Network | needed only for Groq + SerpAPI | Everything else works offline |

### 1.3 Ports

| Service | Port | Command |
|---|---|---|
| FinLens API (B2C) | 8001 | `python -m uvicorn finlens.main:app --port 8001` |
| Money Mule API (B2B) | 8002 | `python -m uvicorn mule.main:app --port 8002` |
| Frontend (Vite dev server, proxies `/x-fin` → 8001, `/x-inst` → 8002) | 5173 | `npm --prefix frontend run dev` |
| mr-mule live feed (optional) | 3000 | `npm start` in `reference/mr-mule` |

### 1.4 External services and configuration (`.env` in the project root)

| Variable | Required? | Purpose | Behaviour if missing |
|---|---|---|---|
| `GROQ_API_KEY` | recommended | LLM explanations, preference extraction, evidence summaries | Template (non-AI) answers, clearly labelled |
| `GROQ_MODEL` | optional | Preferred model (default `openai/gpt-oss-120b`) | Auto-falls back to an available model |
| `SERP_API` | recommended | Live restaurant (Google Maps) and product (Google Shopping) listings | Clearly labelled **DEMO catalog** |
| `DEFAULT_LOCATION` | optional | Restaurant search location | `Bengaluru, Karnataka, India` |
| `SARVAM_API_KEY` | optional | Hindi/Kannada translation and voice (Sarvam AI) | English only; voice buttons report that the key is missing |
| `LOAN_API_BASE_URL` | optional | The team's existing CredWise loan-approval model API | Side panel shows "not configured" |
| `FINLENS_SECRET`, `MULE_SECRET` | change for any shared deployment | Token signing (separate per product) | Dev defaults |
| `FINLENS_DATA_KEY`, `MULE_DATA_KEY` | change for any shared deployment | At-rest encryption keys (separate per product) | Dev defaults |
| `MULE_ANALYST_PASSWORD`, `MULE_LEAD_PASSWORD` | optional | Seeded investigator logins | `analyst-demo-2026` / `lead-demo-2026` |
| `FINLENS_DATABASE_URL`, `MULE_DATABASE_URL` | optional | Separate stores | SQLite files in `data/stores/` |

Groq's free tier allows about 8,000 tokens per minute. The client waits the advised few seconds on HTTP 429, or switches to a second model with its own quota.

---

## 2. Functional requirements: FinLens (B2C)

| ID | Requirement | Blueprint | Status | Where |
|---|---|---|---|---|
| FL-01 | Users upload a bank statement CSV; columns are mapped automatically (Date/Txn Date, Description/Narration, Type+Amount or Debit/Credit, Balance, Status) | §03 | ✅ | `finlens/ingestion.py`, `shared/normalize.py` |
| FL-02 | PDF statements are supported (tables, dated bank lines, UPI-app "Paid to/Received from") using **PyMuPDF + pdfplumber** | §03 (extension) | 🟡 scanned/image PDFs not supported (no OCR) | `finlens/pdf_statement.py` |
| FL-03 | Dates, amounts and debit/credit are normalised; exact duplicates are removed; debit/refund reversal pairs are netted | §03 | ✅ | `ingestion.py` |
| FL-04 | Merchant identification and categorisation into the 8 blueprint categories; unknown items are labelled **Uncertain**, not guessed | §03 | ✅ | `finlens/categorizer.py` |
| FL-05 | A credit is not automatically income: refund, own-transfer, borrowed, P2P and uncertain credits are separated | §03 | ✅ | `categorizer.py` |
| FL-06 | Users can correct a category; the correction applies to that merchant and future uploads | §11 | ✅ | `PATCH /api/transactions/{id}/category` |
| FL-07 | Dashboard: balance, monthly inflow/outflow, category breakdown, essential vs discretionary, month-over-month change | §04, §12 | ✅ | `GET /api/dashboard` |
| FL-08 | Safe-to-spend = balance − pending − upcoming commitments − planned savings − emergency buffer; no assumed future income | §04 | ✅ | `finlens/analytics.py::safe_to_spend` |
| FL-09 | Recurring payment detection tolerant to small date and amount variation; users are invited to review, not told it is wasteful | §04 | ✅ | `analytics.py::detect_recurring` |
| FL-10 | Spending forecast with stated assumptions and uncertainty range | §04 | ✅ | `analytics.py::forecast_month_end` |
| FL-11 | Goal-based savings planner with required contribution and alternative timelines | §04 | ✅ | `GET/POST/PATCH/DELETE /api/goals` |
| FL-12 | Conversational assistant answers only from backend-calculated metrics; the LLM never invents figures | §04, §09, §11 | ✅ every ₹ figure verified; falls back to a template on mismatch | `finlens/assistant.py` |
| FL-13 | Smart Spending: conversational preference collection (category, item, budget, delivery/style/size/specs) | §05 | ✅ | `finlens/smart_spending.py` |
| FL-14 | Search permitted sources and return **five retrieved (not generated) listings** with source link and retrieval timestamp | §05 | ✅ live via SerpAPI; DEMO catalog clearly labelled when no key | `finlens/search.py` |
| FL-15 | Ranking by budget fit, rating quality, preference match and availability, with an explanation per option | §05 | ✅ | `smart_spending.py::rank` |
| FL-16 | Potential savings vs the user's typical comparable spend, labelled as an estimate | §05 | ✅ | `smart_spending.py::savings` |
| FL-17 | FinLens never places orders or purchases | §05, §14 | ✅ | by design |
| FL-18 | Credit: cash-flow indicators (recurring income, volatility, essential spend, obligations, disposable income, balance, trend) | §06 | ✅ | `finlens/credit_features.py` |
| FL-19 | EMI by the reducing-balance formula, with simulation and amortisation | §06 | ✅ | `GET /api/credit/emi` |
| FL-20 | Stress tests (income −20%, essential costs +15%, rate +2 pts, combined) | §06 | ✅ | `credit.py::assess` |
| FL-21 | Output: status (eligible / conditional / not affordable / insufficient information), affordable EMI, illustrative loan sizes, supporting and limiting factors, missing info | §06 | ✅ | `POST /api/credit/assess` |
| FL-22 | Model-validation rule: the model's label must be stated; no claim of proven default prediction | §06, §10 | ✅ real repayment label (1.32M Lending Club loans), out-of-time test, leakage audit, model card | `ml/train_credit_lendingclub.py`, `docs/CREDIT_MODEL_CARD.md` |
| FL-23 | Separate explicit consent per purpose (analytics, smart spending, credit) | §08, §11 | ✅ | `POST /api/consents` |
| FL-24 | Users can delete their data and their account | §11 | ✅ | `DELETE /api/data`, `DELETE /api/account` |
| FL-26 | Personal profile captured at onboarding (occupation, income sources, payday, living, fixed costs, goal, diet, sizes, priorities) and used by every feature | user request | ✅ | `finlens/profile.py`, `ProfileWizard.jsx` |
| FL-27 | Goals with deadlines and pace drive budgets, recommendations and answers (reserved savings, tighter budgets when behind, per-purchase goal impact, realistic-date warning) | §04 + user request | ✅ | `finlens/goals.py`, `finlens/room.py` |
| FL-28 | Smart Spending uses balance/safe-to-spend/usual spend to suggest and sanity-check budgets; group orders handled per person | §05 + user request | ✅ | `smart_spending.py`, `room.py` |
| FL-29 | Built-in lender-style approval estimate (replaces the unavailable CredWise API) with explanations and improvement tips | extension | ✅ 92.2% test accuracy | `finlens/lender.py`, `ml/train_lender_model.py` |
| FL-30 | Money-health verdict (healthy / steady / tight / at risk) drives answers, nudges and suggestions; the assistant never claims the user is running out of money unless the verdict says so | user request | ✅ | `analytics.py::money_health`, `assistant.py` |
| FL-31 | Budgets above balance / safe-to-spend / plausible range require explicit confirmation before any search; typo correction offered | user request | ✅ | `smart_spending.py::budget_guard` |
| FL-32 | Proactive, dismissible nudges and a weekly money report | extension | ✅ | `finlens/nudges.py`, `GET /api/nudges`, `GET /api/report/weekly` |
| FL-33 | Split expenses with friends; only the user's share counts in analytics; paybacks are not income; payback detection after large payments (next 10 incoming transfers matched to an even split) | extension + user request | ✅ | `finlens/split.py`, `/api/split*` |
| FL-34 | Credit-builder path for first-time borrowers with persisted checklist and lender-model timeline | extension | ✅ | `finlens/credit_builder.py`, `/api/credit/builder` |
| FL-35 | Hindi and Kannada (translation) and voice input/output | extension | ✅ requires `SARVAM_API_KEY` | `shared/sarvam.py`, `/api/voice/*`, `frontend/src/i18n.jsx`, `voice.jsx` |
| FL-25 | Live Account Aggregator integration | §03 | ⏭️ future; CSV/PDF upload only, no bank passwords | n/a |

## 3. Functional requirements: Money Mule Detection (B2B)

| ID | Requirement | Blueprint | Status | Where |
|---|---|---|---|---|
| MM-01 | Upload multi-account transaction records; normalise and validate (invalid, self-transfer and duplicate rows dropped and reported) | §07 | ✅ | `mule/features.py::normalize_transactions` |
| MM-02 | Accounts as nodes, transactions as directed edges, G = (V, E) | §07 | ✅ | NetworkX `MultiDiGraph` |
| MM-03 | Detect fan-in, fan-out, rapid pass-through, circular flow, velocity, dormant activation, amount splitting | §07 | ✅ | `mule/features.py` |
| MM-04 | Additional patterns from the team's MuleGuard: layered shell chains, 72h smurfing, high-volume merchant false-positive suppression | §07 (extension) | ✅ | `mule/patterns.py` |
| MM-05 | Three layers (rules, ML, graph) combined as evidence, **not averaged**, with documented priority rules | §07 | ✅ | `mule/detection.py` |
| MM-06 | Explainable, evidence-linked alerts (counts, windows, amounts, paths) | §07 | ✅ | `features.py::explain` |
| MM-07 | Investigator dashboard: overview, searchable alerts, interactive network graph, in/out trails, time-ordered activity | §07, §12 | ✅ | `frontend/src/institution/*` |
| MM-08 | Investigation notes, status workflow, report export | §07 | ✅ | `/api/alerts/{id}/notes`, `/status`, `/report` |
| MM-09 | Human review before consequential action; no automatic freezing; no labelling people as criminals | §07, §11, §14 | ✅ final dispositions are lead-only | `mule/main.py` |
| MM-10 | Role-based access (analyst / lead) and an audit log of data access and case activity | §11 | ✅ | `GET /api/audit` |
| MM-11 | Restricted account identifiers | §11 | ✅ aliases by default; lead-only reveal with logged reason | `POST …/reveal` |
| MM-12 | Fraud-ring candidates with risk scores and a MuleGuard-format JSON export | extension | ✅ | `/rings`, `/export.json` |
| MM-13 | Live ingest from a WebSocket CSV feed (mr-mule compatible) | extension | ✅ | `POST /api/datasets/live`, `/append` |
| MM-16 | Built-in live simulator with realistic account history, on-demand pattern injection, WebSocket alert push (toasts) and animated money-trail replay | extension | ✅ | `mule/live.py`, `/api/live/*`, `/ws/live/{id}`, `LiveContext.jsx`, `Graph.jsx` |
| MM-14 | Evaluation against labels: precision, recall, PR-AUC, false-positive rate; never accuracy alone | §10 | ✅ | `POST /api/datasets/{id}/evaluate`, `docs/MULE_MODEL_CARD.md` |
| MM-15 | Use the public AML benchmark (AMLworld/AMLSim) and synthetic scenarios with known patterns | §10 | ✅ trained on IBM AMLworld HI-Medium (31.9M txns), tested on HI-Small and LI-Small | `ml/train_mule_ibm.py`, `ml/generate_mule_data.py` |

## 4. Non-functional requirements

| ID | Requirement | Blueprint | Status / evidence |
|---|---|---|---|
| NF-01 | **Purpose separation:** separate databases, auth, secrets and encryption keys per product; no FinLens data in investigations; no AML score in credit | §02, §08, §11 | ✅ separate stores/keys; cross-product tokens rejected (tested) |
| NF-02 | Encryption at rest for uploaded files, transaction descriptions and real account IDs | §11 | ✅ Fernet (AES-128-CBC + HMAC) |
| NF-03 | Passwords hashed | security | ✅ PBKDF2-SHA256, 200k rounds |
| NF-04 | Minimum necessary data sent to the LLM | §11 | ✅ computed facts only, never the raw statement |
| NF-05 | Backend owns all calculations; the LLM only interprets | §09 | ✅ figure verification on every AI answer |
| NF-06 | Synthetic data clearly labelled; no unmeasured claims | §10, §14 | ✅ "SYNTHETIC"/"DEMO" labels; model cards |
| NF-07 | Time-based / entity-aware validation; baselines reported | §10 | ✅ temporal split (credit), ring-aware folds + held-out scenario (mule), rules/LR baselines |
| NF-08 | Performance at prototype scale | n/a | ✅ ~8.4k transactions analysed in about 2–3 s; dashboard < 1 s |
| NF-09 | Works offline without API keys (graceful degradation) | n/a | ✅ template answers + DEMO catalog |
| NF-10 | Responsive, accessible UI; respects reduced motion | n/a | ✅ layouts collapse under 980 px; `prefers-reduced-motion` honoured |
| NF-11 | Automated tests | §13 | ✅ 36 pytest tests (`python -m pytest`) |

## 5. Out of scope (per blueprint §14)

- Automatic bank login using user passwords
- Automatic food orders or purchases
- Automatic account freezing or any autonomous enforcement
- Guaranteed loan approval (lender underwriting remains separate)
- Claims of a live banking partnership or regulatory registration
- Claims of production-grade accuracy from synthetic data
