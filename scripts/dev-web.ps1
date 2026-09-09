# Serves the pages for local work, in a second window.
#
#     .\scripts\dev-web.ps1
#
# The pages have to come from a web server rather than being opened as
# files: shared.js is loaded with a <script src>, and a file:// page
# can't fetch the API. Any static server does — this uses the one
# Python already ships with.

$frontend = Join-Path $PSScriptRoot "..\frontend"

Write-Host "Pages    http://localhost:5500/member.html?as=YourName" -ForegroundColor Cyan
Write-Host "Manage   http://localhost:5500/organizer.html?as=YourName" -ForegroundColor Cyan
Write-Host "Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

Push-Location $frontend
try {
    python -m http.server 5500
}
finally {
    Pop-Location
}
