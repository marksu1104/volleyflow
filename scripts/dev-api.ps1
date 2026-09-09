# Starts the API for local work, with the local sign-in turned on.
#
# Exists because the equivalent one-liner is different in every shell:
# `VAR=1 cmd` is bash, and Windows PowerShell rejects it outright — as
# does `&&`. Rather than document three variants and get one wrong,
# run this.
#
#     .\scripts\dev-api.ps1
#
# VOLLEYFLOW_DEV_LOGIN is what lets ?as=<name> work without LINE. It is
# set only for this process, so it can't leak into anything else, and
# production never sets it at all. See README, "Being somebody, without
# LINE".

$env:VOLLEYFLOW_DEV_LOGIN = "1"

Write-Host "API      http://localhost:8000" -ForegroundColor Cyan
Write-Host "API docs http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "Local sign-in is ON for this window. Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

uv run uvicorn volleyflow.api.main:app --reload --port 8000
