"""Download the public datasets used to train FinLens / MuleScope models.

Everything (including the Hugging Face cache) is stored under data/external/.
Sources are public Hugging Face mirrors of the original releases:

  IBM Transactions for Anti Money Laundering (AMLworld, Altman et al., NeurIPS 2023)
    original: https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml
    licence : CDLA-Sharing-1.0
    HI-Medium + HI-Small  <- hf: OsamaMIT/IBM-AML-HI
    LI-Small              <- hf: LordNR/AMLGraphX-IBM-AML (zip)

  Lending Club accepted loans 2007-2018Q4 (real loans, real outcomes)
    licence : CC0-1.0  <- hf: codesignal/lending-club-loan-accepted

  Home Credit Default Risk - application_train (real applicants, real outcome)
    <- hf: mohameddhameem/home-credit-default-risk

  Realistic Loan Approval Dataset (US & Canada), Parth Patel / Kaggle - 50k lender decisions (synthetic)
    <- hf: kaopanboonyuen/loan-approval-us-ca

Usage:  python -m ml.download_datasets [--only ibm|lending|homecredit]
"""
from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "data" / "external"
EXT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(EXT / ".hf-home"))  # keep any cache inside the project
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import HfApi, hf_hub_download  # noqa: E402

FILES = {
    "ibm": [
        ("OsamaMIT/IBM-AML-HI", "HI-Small_Trans.csv", "ibm_aml"),
        ("OsamaMIT/IBM-AML-HI", "HI-Small_Patterns.txt", "ibm_aml"),
        ("OsamaMIT/IBM-AML-HI", "HI-Small_accounts.csv", "ibm_aml"),
        ("OsamaMIT/IBM-AML-HI", "HI-Medium_Trans.csv", "ibm_aml"),
        ("OsamaMIT/IBM-AML-HI", "HI-Medium_Patterns.txt", "ibm_aml"),
        ("OsamaMIT/IBM-AML-HI", "HI-Medium_accounts.csv", "ibm_aml"),
        ("LordNR/AMLGraphX-IBM-AML", "IBM-AML-LI-Small.zip", "ibm_aml"),
    ],
    "lending": [("codesignal/lending-club-loan-accepted", "accepted_2007_to_2018Q4.csv", "lending_club")],
    "homecredit": [],  # resolved at runtime (application_train parquet shard names)
    "approval": [("kaopanboonyuen/loan-approval-us-ca", f"data/{x}.parquet", "loan_approval/kaopanboonyuen") for x in ("train", "validation", "test")],
}


def fetch(repo: str, filename: str, sub: str) -> Path:
    dest = EXT / sub
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / filename
    if target.exists() and target.stat().st_size > 0:
        print(f"  ✓ {sub}/{filename} already present ({target.stat().st_size/1e6:,.0f} MB)")
        return target
    print(f"  ↓ {repo} :: {filename}")
    p = hf_hub_download(repo_id=repo, filename=filename, repo_type="dataset", local_dir=dest)
    print(f"    saved {Path(p).stat().st_size/1e6:,.0f} MB")
    return Path(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["ibm", "lending", "homecredit", "approval"])
    a = ap.parse_args()
    groups = [a.only] if a.only else ["lending", "homecredit", "approval", "ibm"]
    for g in groups:
        print(f"[{g}]")
        if g == "homecredit":
            api = HfApi()
            info = api.dataset_info("mohameddhameem/home-credit-default-risk")
            for s in info.siblings:
                if s.rfilename.startswith("application_train"):  # folder is application_train_dated/
                    fetch("mohameddhameem/home-credit-default-risk", s.rfilename, "home_credit")
            continue
        for repo, fn, sub in FILES[g]:
            p = fetch(repo, fn, sub)
            if p.suffix == ".zip":
                with zipfile.ZipFile(p) as z:
                    names = z.namelist()
                    print(f"    unzipping {len(names)} file(s): {names[:4]}")
                    z.extractall(p.parent)
    print("done")


if __name__ == "__main__":
    main()
