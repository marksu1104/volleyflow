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

. (Join-Path $PSScriptRoot "lan-address.ps1")
$lan = Get-LanAddress

Write-Host "API      http://localhost:8000" -ForegroundColor Cyan
if ($lan) { Write-Host "         http://${lan}:8000   (from your phone)" -ForegroundColor Cyan }
Write-Host "Local sign-in is ON for this window. Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

# --host 0.0.0.0 so the phone can reach it. uvicorn otherwise binds
# 127.0.0.1, which only this machine can talk to — the page loads on the
# phone and every API call it makes then fails, because on the phone
# "localhost" is the phone. Paired with VOLLEYFLOW_DEV_LOGIN above this
# does mean anyone on this wifi can be anyone in the *dev* database, so
# it stays in this script and never in a deployment.
uv run uvicorn volleyflow.api.main:app --reload --host 0.0.0.0 --port 8000
