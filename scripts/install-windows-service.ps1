#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$ServiceName  = "AppTrack"
$DisplayName  = "AppTrack Server"
$Description  = "AppTrack CAD Recording and Tutorial Backend"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot  = Split-Path -Parent $ScriptDir
$BackendDir   = Join-Path $ProjectRoot "backend"
$FrontendDist = Join-Path $ProjectRoot "frontend\dist"
$PythonExe    = Join-Path $BackendDir "venv\Scripts\python.exe"
$LogDir       = Join-Path $ProjectRoot "logs"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Error "nssm not found in PATH. Ensure C:\tools\nssm is in the system PATH."
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python venv not found at: $PythonExe`nRun: cd backend && python -m venv venv && venv\Scripts\activate && pip install -r requirements-server.txt"
    exit 1
}

if (-not (Test-Path $FrontendDist)) {
    Write-Error "Frontend dist not found at: $FrontendDist`nRun: cd frontend && npm install && npm run build"
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ---------------------------------------------------------------------------
# Build environment variable list
# ---------------------------------------------------------------------------

$EnvVars = @(
    "APPTRACK_FRONTEND_DIST=$FrontendDist",
    "PYTHONPATH=$BackendDir",
    "PYTHONUNBUFFERED=1"
)

$EnvFile = Join-Path $BackendDir ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and ($line -notmatch '^\s*#') -and ($line -match '=')) {
            $EnvVars += $line
        }
    }
}

# ---------------------------------------------------------------------------
# Remove existing service if present
# ---------------------------------------------------------------------------

$statusOutput = & nssm status $ServiceName 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Stopping and removing existing '$ServiceName' service..."
    & nssm stop   $ServiceName 2>&1 | Out-Null
    & nssm remove $ServiceName confirm 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}

# ---------------------------------------------------------------------------
# Install service
# ---------------------------------------------------------------------------

$UvicornArgs = "-m uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 2"

Write-Host "Installing service '$ServiceName'..."

& nssm install     $ServiceName $PythonExe $UvicornArgs
& nssm set         $ServiceName AppDirectory   $BackendDir
& nssm set         $ServiceName DisplayName    $DisplayName
& nssm set         $ServiceName Description    $Description
& nssm set         $ServiceName Start          SERVICE_AUTO_START

& nssm set         $ServiceName AppStdout      (Join-Path $LogDir "apptrack.log")
& nssm set         $ServiceName AppStderr      (Join-Path $LogDir "apptrack-error.log")
& nssm set         $ServiceName AppRotateFiles 1
& nssm set         $ServiceName AppRotateSeconds 86400
& nssm set         $ServiceName AppRotateBytes  10485760

& nssm set         $ServiceName AppRestartDelay 3000

# Set environment variables via registry (REG_MULTI_SZ — most reliable method)
$RegPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
Set-ItemProperty -Path $RegPath -Name "AppEnvironmentExtra" -Value $EnvVars -Type MultiString

# ---------------------------------------------------------------------------
# Start service
# ---------------------------------------------------------------------------

Write-Host "Starting service '$ServiceName'..."
& nssm start $ServiceName

Start-Sleep -Seconds 3
$status = & nssm status $ServiceName 2>&1
Write-Host "Service status: $status"

Write-Host ""
Write-Host "Done. AppTrack is running at http://localhost:8001"
Write-Host "Gallery : http://localhost:8001/gallery"
Write-Host "API docs: http://localhost:8001/docs"
Write-Host "Logs    : $LogDir"
