#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$ServiceName = "AppTrack"

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Error "nssm not found in PATH."
    exit 1
}

$statusOutput = & nssm status $ServiceName 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Service '$ServiceName' does not exist. Nothing to do."
    exit 0
}

Write-Host "Stopping service '$ServiceName'..."
& nssm stop $ServiceName 2>&1 | Out-Null
Start-Sleep -Seconds 3

Write-Host "Removing service '$ServiceName'..."
& nssm remove $ServiceName confirm

Write-Host "Service '$ServiceName' has been removed."
