"""Cash-flow features for Alternative Credit Intelligence (shared by training and inference)."""
from __future__ import annotations

import numpy as np
import pandas as pd

CREDIT_FEATURES = [
    "avg_monthly_income", "income_cv", "essential_ratio", "discretionary_ratio", "obligation_ratio",
    "savings_rate", "balance_to_income", "cashflow_trend", "months_history", "proposed_emi_ratio",
]


def extract(df: pd.DataFrame, proposed_emi: float = 0.0, declared_obligations: float = 0.0) -> dict:
    """Compute indicators from a user's normalized transactions (complete months only when possible)."""
    from .analytics import monthly_summary, spend_df  # local import avoids a cycle

    months = monthly_summary(df)
    if len(months) >= 3:
        months = months[:-1] if _partial_last_month(df) else months
    inc = np.array([m["income"] for m in months], dtype=float)
    ess = np.array([m["essential"] for m in months], dtype=float)
    dis = np.array([m["discretionary"] for m in months], dtype=float)
    out_ = np.array([m["outflow"] for m in months], dtype=float)
    net = inc - out_

    sp = spend_df(df)
    obligations = sp[sp["subcategory"].isin(["Loan repayment", "Card repayment"])]
    obl_monthly = (obligations.groupby("month")["amount"].sum().mean() if len(obligations) else 0.0) + declared_obligations

    avg_inc = float(inc.mean()) if len(inc) else 0.0
    bal = df[df["balance"].notna()]["balance"] if "balance" in df else pd.Series(dtype=float)
    trend = float(np.polyfit(np.arange(len(net)), net, 1)[0] / avg_inc) if len(net) >= 2 and avg_inc > 0 else 0.0

    feats = {
        "avg_monthly_income": avg_inc,
        "income_cv": float(inc.std(ddof=0) / avg_inc) if avg_inc > 0 else 1.0,
        "essential_ratio": float(ess.mean() / avg_inc) if avg_inc > 0 else 1.0,
        "discretionary_ratio": float(dis.mean() / avg_inc) if avg_inc > 0 else 1.0,
        "obligation_ratio": float(obl_monthly / avg_inc) if avg_inc > 0 else 1.0,
        "savings_rate": float(net.mean() / avg_inc) if avg_inc > 0 else -1.0,
        "balance_to_income": float(bal.mean() / avg_inc) if avg_inc > 0 and len(bal) else 0.0,
        "cashflow_trend": max(min(trend, 1.0), -1.0),
        "months_history": len(months),
        "proposed_emi_ratio": float(proposed_emi / avg_inc) if avg_inc > 0 else 1.0,
    }
    detail = {
        "months_used": [m["month"] for m in months],
        "monthly_income": inc.round(2).tolist(),
        "monthly_essential": ess.round(2).tolist(),
        "monthly_discretionary": dis.round(2).tolist(),
        "existing_obligations_monthly": round(float(obl_monthly), 2),
        "average_balance": round(float(bal.mean()), 2) if len(bal) else None,
    }
    return {"features": feats, "detail": detail}


def _partial_last_month(df: pd.DataFrame) -> bool:
    last = df["date"].max()
    return last.day < 25
