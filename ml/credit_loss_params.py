"""Empirical loss-given-default (LGD) for unsecured personal loans, from Lending Club charged-off loans.

    EAD_default = funded_amnt - total_rec_prncp            (principal still owed when the loan defaulted)
    net_loss    = EAD_default - (recoveries - collection_recovery_fee)
    LGD         = net_loss / EAD_default                     (clipped to [0, 1])

Used by FinLens for Expected Loss = PD x LGD x EAD. Writes ml/artifacts/credit_loss_params.json.
Usage:  python -m ml.credit_loss_params
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LC = ROOT / "data" / "external" / "lending_club" / "accepted_2007_to_2018Q4.csv"
OUT = ROOT / "ml" / "artifacts" / "credit_loss_params.json"


def main():
    t0 = time.time()
    cols = ["funded_amnt", "total_rec_prncp", "recoveries", "collection_recovery_fee", "loan_status", "term", "issue_d"]
    df = pd.read_csv(LC, usecols=cols, engine="pyarrow")
    bad = df[df["loan_status"].isin(["Charged Off", "Default", "Does not meet the credit policy. Status:Charged Off"])].copy()
    bad["ead"] = (bad["funded_amnt"] - bad["total_rec_prncp"]).clip(lower=0)
    bad = bad[bad["ead"] > 0]
    net_rec = (bad["recoveries"].fillna(0) - bad["collection_recovery_fee"].fillna(0)).clip(lower=0)
    bad["lgd"] = ((bad["ead"] - net_rec) / bad["ead"]).clip(0, 1)
    bad["ead_share"] = bad["ead"] / bad["funded_amnt"]
    bad["term_m"] = bad["term"].astype(str).str.extract(r"(\d+)")[0].astype(float)
    by_term = {f"{int(t)}_months": {"loans": int(len(g)), "lgd_mean": round(float(g["lgd"].mean()), 4),
                                     "lgd_median": round(float(g["lgd"].median()), 4),
                                     "ead_share_at_default_mean": round(float(g["ead_share"].mean()), 4)}
               for t, g in bad.groupby("term_m")}
    # exposure-weighted LGD (what a lender actually loses per rupee outstanding)
    lgd_w = float((bad["lgd"] * bad["ead"]).sum() / bad["ead"].sum())
    out = {
        "source": "Lending Club accepted loans 2007-2018Q4 (real loans, real recoveries; CC0)",
        "charged_off_loans": int(len(bad)),
        "lgd_mean": round(float(bad["lgd"].mean()), 4), "lgd_median": round(float(bad["lgd"].median()), 4),
        "lgd_exposure_weighted": round(lgd_w, 4),
        "lgd_p10_p90": [round(float(bad["lgd"].quantile(0.1)), 4), round(float(bad["lgd"].quantile(0.9)), 4)],
        "ead_share_at_default_mean": round(float(bad["ead_share"].mean()), 4),
        "by_term": by_term,
        "formula": "LGD = (principal outstanding at default - net recoveries) / principal outstanding at default",
        "caveat": "US unsecured consumer loans; Indian recovery rates differ (collections, legal process). Used as a transparent, data-based assumption.",
        "seconds": round(time.time() - t0, 1),
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
