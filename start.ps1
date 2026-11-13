# Starts FinLens API (8001), Money Mule API (8002) and the React frontend (5173) in separate windows.
# Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = if ($env:FINLENS_PYTHON) { $env:FINLENS_PYTHON } else { "C:\Users\cbsai\AppData\Local\Programs\Python\Python313\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }

# Trained models ship in ml/artifacts. Only if they are missing:
#   - with the public datasets present (python -m ml.download_datasets, ~6 GB) -> train on real-scale data
#   - otherwise -> quick synthetic fallback so the app still runs
if (-not (Test-Path "$root\ml\artifacts\mule_model.joblib") -or -not (Test-Path "$root\ml\artifacts\credit_model.joblib")) {
  Push-Location $root
  if (-not (Test-Path "$root\data\samples\mule_demo_transactions.csv")) { & $py -m ml.generate_finlens_samples; & $py -m ml.train_mule_model }
  if (Test-Path "$root\data\external\lending_club\accepted_2007_to_2018Q4.csv") {
    Write-Host "Training credit model on Lending Club (real outcomes)..."; & $py -m ml.train_credit_lendingclub
  } else { & $py -m ml.train_credit_model; Copy-Item "$root\ml\artifacts\credit_model_synthetic.joblib" "$root\ml\artifacts\credit_model.joblib" }
  if (Test-Path "$root\data\external\ibm_aml\HI-Medium_Trans.csv") {
    Write-Host "Training mule model on IBM AMLworld (~15 min)..."; & $py -m ml.train_mule_ibm
  } else { & $py -m ml.train_mule_model; Copy-Item "$root\ml\artifacts\mule_model_synthetic.joblib" "$root\ml\artifacts\mule_model.joblib" }
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
