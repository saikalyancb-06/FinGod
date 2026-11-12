"""Configuration loaded from the project-level .env file."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
STORE_DIR = DATA_DIR / "stores"          # databases live here, one per product
ARTIFACT_DIR = ROOT / "ml" / "artifacts"  # trained models + model cards
STORE_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    return val if val not in (None, "") else default


# --- LLM (Groq, OpenAI-compatible endpoint) ---
GROQ_API_KEY = env("GROQ_API_KEY")
GROQ_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# --- Search (SerpAPI) - accept either spelling ---
SERP_API_KEY = env("SERP_API") or env("SERPAPI_KEY") or env("SERP_API_KEY")
DEFAULT_LOCATION = env("DEFAULT_LOCATION", "Bengaluru, Karnataka, India")

# --- Optional: the team's existing CredWise loan-approval model API (POST /api/loan/predict) ---
LOAN_API_BASE_URL = env("LOAN_API_BASE_URL")

# --- Purpose-separated stores (SQLite now, Postgres by changing the URL) ---
FINLENS_DATABASE_URL = env("FINLENS_DATABASE_URL", f"sqlite:///{(STORE_DIR / 'finlens.db').as_posix()}")
MULE_DATABASE_URL = env("MULE_DATABASE_URL", f"sqlite:///{(STORE_DIR / 'mule.db').as_posix()}")

# --- Separate signing secrets: a FinLens token is never valid on the institutional API ---
FINLENS_SECRET = env("FINLENS_SECRET", "dev-finlens-secret-change-me")
MULE_SECRET = env("MULE_SECRET", "dev-mule-secret-change-me")

# --- Encryption passphrases for data at rest (Fernet keys are derived from these) ---
FINLENS_DATA_KEY = env("FINLENS_DATA_KEY", "dev-finlens-data-key-change-me")
MULE_DATA_KEY = env("MULE_DATA_KEY", "dev-mule-data-key-change-me")

CORS_ORIGINS = [o.strip() for o in env("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")]
