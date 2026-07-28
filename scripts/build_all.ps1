# Full build: venv deps, graph packs, C++ core, tests.
# Run from the repository root:  powershell -File scripts\build_all.ps1
$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv)) {
    python -m venv .venv
}
# requirements-dev.txt pulls in runtime + ingestion + test/lint tooling.
# The API image installs only requirements.txt (no osmnx); pack building and
# pytest need more than that.
.\.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -q -e ./core

# Graph packs (cached: re-runs use data/cache/*.graphml, no Overpass hit)
.\.venv\Scripts\python.exe -m ingestion.build_pack --region berkeley_small
.\.venv\Scripts\python.exe -m ingestion.build_pack --region berkeley_oakland

.\.venv\Scripts\python.exe -m pytest tests/ -q

Write-Host "`nAll built. Start the stack with:"
Write-Host "  .venv\Scripts\python.exe -m uvicorn api.main:app --port 8000"
Write-Host "  npm --prefix web run dev"
