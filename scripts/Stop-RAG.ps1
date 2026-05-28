#Requires -Version 5.1
<#
.SYNOPSIS
    Stop Enterprise RAG services (Windows PowerShell)

.PARAMETER Reset
    Also delete all data volumes (full reset — DESTRUCTIVE)

.EXAMPLE
    .\scripts\Stop-RAG.ps1           # Stop, keep data
    .\scripts\Stop-RAG.ps1 -Reset    # Stop + wipe all data
#>
param([switch]$Reset)

Set-StrictMode -Version Latest
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Split-Path -Parent $ScriptDir)

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "  Enterprise RAG v2.0.8 — Stop Services" -ForegroundColor White
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host ""

try { $null = docker info 2>&1 } catch {
    Write-Host "  Docker is not running — nothing to stop." -ForegroundColor Yellow
    exit 0
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Docker is not running — nothing to stop." -ForegroundColor Yellow
    exit 0
}

if ($Reset) {
    Write-Host "  *** FULL RESET — All data volumes will be deleted! ***" -ForegroundColor Red
    Write-Host ""
    Write-Host "  This permanently deletes:" -ForegroundColor Yellow
    Write-Host "    - SQL Server database (users, roles, sources, chat history)"
    Write-Host "    - PGVector embeddings (all indexed document chunks)"
    Write-Host "    - Open WebUI data (conversations, user settings)"
    Write-Host "    - Uploaded source files"
    Write-Host ""
    $confirm = Read-Host "  Type YES to confirm full reset"
    if ($confirm -ne "YES") {
        Write-Host "  Cancelled." -ForegroundColor Yellow
        exit 0
    }
    Write-Host ""
    Write-Host "  Removing all containers and volumes..." -ForegroundColor Yellow
    docker compose down -v --remove-orphans
    Write-Host ""
    Write-Host "  Full reset complete. All data deleted." -ForegroundColor Green
    Write-Host "  Run .\scripts\Start-RAG.ps1 to set up fresh."
} else {
    Write-Host "  Stopping all containers (data volumes preserved)..." -ForegroundColor Cyan
    docker compose down --remove-orphans
    Write-Host ""
    Write-Host "  All services stopped. Data volumes preserved." -ForegroundColor Green
    Write-Host "  Restart : .\scripts\Start-RAG.ps1 -SkipBuild"
    Write-Host "  Full reset: .\scripts\Stop-RAG.ps1 -Reset"
}
Write-Host ""
