# Money Mule Classifier — Model Card

**Version:** mule-xgb-ibm-202611131914 · trained 2026-11-13 19:14:42 · 4.1 min

## Training data
* **IBM AMLworld HI-Medium (Altman et al., NeurIPS 2023; CDLA-Sharing-1.0) + 10 synthetic INR scenarios (weight 15.0)** — a large multi-agent simulation of a financial ecosystem with injected
  laundering patterns (fan-in/out, gather-scatter, scatter-gather, cycles, bipartite, stacks, random).
  There is no public *real* bank AML dataset; AMLworld is the standard public benchmark.
* **Label:** account participates in >=1 transaction flagged Is Laundering.
* **Features:** `mule/model_features.py` — 25 vectorised, scale-free account features
  (counts/degrees, amounts relative to the dataset median, forwarding speed, daily fan-in/out,
  velocity, night share, reciprocity, PageRank, strongly-connected-component size). The **same code**
  computes features for uploads in the app, so there is no train/serve skew.
* XGBoost (712 trees, depth 7, class-weighted), early-stopped on a 20 % stratified validation split;
  decision threshold = F1-optimal on that split (0.9122).

## Results
Held-out rows are **separate generated worlds** (different accounts, banks and patterns), not a random split.

| Evaluation set | Accounts | Positive accounts | PR-AUC | Random PR-AUC | LogReg PR-AUC | ROC-AUC | Precision @thr | Recall @thr | FPR @thr | Precision@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| validation_ibm | 351,720 | 8,371 (2.38%) | **0.3203** | 0.0238 | 0.0757 | 0.8716 | 0.6595 | 0.1892 | 0.00238 | 1.0 |
| validation_inr | 2,591 | 290 (11.19%) | **0.9787** | 0.11193 | 0.5032 | 0.9928 | 0.9747 | 0.931 | 0.00304 | 1.0 |
| heldout_HI-Small | 422,734 | 6,357 (1.50%) | **0.2267** | 0.01504 | 0.0535 | 0.8556 | 0.3421 | 0.1955 | 0.00574 | 1.0 |
| heldout_LI-Small | 576,781 | 5,304 (0.92%) | **0.0694** | 0.0092 | 0.0301 | 0.7822 | 0.04 | 0.1914 | 0.04261 | 1.0 |
| heldout_synthetic_INR_seed2 | 967 | 100 (10.34%) | **0.9915** | 0.10341 | 0.5482 | 0.9988 | 0.9596 | 0.95 | 0.00461 | 0.96 |
| app_demo_synthetic_INR | 374 | 40 (10.70%) | **0.9809** | 0.10695 | — | 0.996 | 1.0 | 0.925 | 0.0 | 0.4 |

PR-AUC is the primary metric (labels are heavily imbalanced; accuracy is not reported).

## Top features
`[["in_cycle", 0.1559], ["active_days_share", 0.1099], ["flow_balance", 0.1058], ["log_scc_size", 0.0931], ["fan_ratio", 0.0799], ["rel_avg_out", 0.0516], ["log_out_degree", 0.0426], ["repeat_ratio", 0.0355], ["log_in_degree", 0.0345], ["rel_avg_in", 0.0286]]`

## Limitations
* AMLworld is synthetic; results show the method works on a realistic benchmark, **not** real-bank performance.
* The app's INR demo data is a different domain (row above); thresholds must be re-validated on
  authorised institutional data before any use.
* The ML score is one of three evidence layers; alerts are prioritised by explicit rules and always need
  human review. The system never freezes accounts or labels people as criminals.
