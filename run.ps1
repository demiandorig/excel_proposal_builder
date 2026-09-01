# Start the Entravision Proposal Builder web app (Windows PowerShell)
# Usage: .\run.ps1 [-Port 8000]
param([int]$Port = 8000)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Activate venv
$activate = Join-Path $ScriptDir ".venv\Scripts\Activate.ps1"
if (Test-Path $activate) { & $activate }

# Load .env if present
$envFile = Join-Path $ScriptDir ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim())
        }
    }
}

$env:PYTHONPATH = $ScriptDir

$driveEnabled = $env:DRIVE_ROOT_FOLDER_ID -and $env:DRIVE_CLIENT_ID
Write-Host "================================================================"
Write-Host " Entravision Proposal Builder"
Write-Host "================================================================"
Write-Host " Open: http://127.0.0.1:$Port"
Write-Host " API docs: http://127.0.0.1:$Port/docs"
if ($driveEnabled) {
    Write-Host " Drive upload: ENABLED (OAuth2 — first use will prompt authorization)"
} else {
    Write-Host " Drive upload: disabled (set DRIVE_CLIENT_ID, DRIVE_CLIENT_SECRET, DRIVE_ROOT_FOLDER_ID in .env)"
}
Write-Host "================================================================"

uvicorn app.main:app --host 0.0.0.0 --port $Port --reload
