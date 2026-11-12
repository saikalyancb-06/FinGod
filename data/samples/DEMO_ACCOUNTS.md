# Local demo accounts (test values only — local dev databases)

| Product | Login | Password | Notes |
|---|---|---|---|
| FinLens (B2C) | demo.student@example.com | finlens-demo-2026 | Created during verification; recreate via "Create account" if you reset `data/stores/finlens.db`. |
| Money Mule (B2B) | analyst | value of `MULE_ANALYST_PASSWORD` in `.env` (default `analyst-demo-2026`) | Seeded automatically on first start. |
| Money Mule (B2B) | lead | value of `MULE_LEAD_PASSWORD` in `.env` (default `lead-demo-2026`) | Can record final dispositions, reveal identifiers, view audit log. |

These are demo credentials for a local prototype. Change them before any shared deployment.
