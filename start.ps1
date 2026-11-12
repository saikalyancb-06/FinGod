# Starts FinLens API (8001), Money Mule API (8002) and the React frontend (5173) in separate windows.
# Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = if ($env:FINLENS_PYTHON) { $env:FINLENS_PYTHON } else { "C:\Users\cbsai\AppData\Local\Programs\Python\Python313\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }

if (-not (Test-Path "$root\ml\artifacts\mule_model.joblib") -or -not (Test-Path "$root\ml\artifacts\credit_model.joblib")) {
  Write-Host "Training models on synthetic data (first run)..."
  Push-Location $root
  & $py -m ml.generate_finlens_samples
  & $py -m ml.train_mule_model
  & $py -m ml.train_credit_model
  Pop-Location
}
if (-not (Test-Path "$root\frontend\node_modules")) {
  Push-Location "$root\frontend"; npm install --cache "$root\.npm-cache"; Pop-Location
}
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; & '$py' -m uvicorn finlens.main:app --port 8001"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; & '$py' -m uvicorn mule.main:app --port 8002"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm run dev"
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173"
