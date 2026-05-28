#Requires -Version 5.1
<#
.SYNOPSIS
    Enterprise RAG v2.0.8 - Windows PowerShell Startup Script

.DESCRIPTION
    SQL Server and PostgreSQL/pgvector are EXTERNAL - this script does NOT
    start them. It verifies they are reachable, then starts the five
    Dockerised services: model-server, backend, open-webui, owui-auth-proxy, nginx.

.PARAMETER SkipBuild
    Skip "docker compose build" - use when images are already up to date.

.PARAMETER SkipModelCheck
    Skip the embedding model volume check.

.PARAMETER Logs
    Tail all service logs immediately after startup.

.PARAMETER Down
    Stop all services and exit.

.EXAMPLE
    .\scripts\Start-RAG.ps1
    .\scripts\Start-RAG.ps1 -SkipBuild
    .\scripts\Start-RAG.ps1 -SkipBuild -SkipModelCheck
    .\scripts\Start-RAG.ps1 -Down
#>
param(
    [switch]$SkipBuild,
    [switch]$SkipModelCheck,
    [switch]$Logs,
    [switch]$Down
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# Colour helpers
function Write-Step  { param($n,$t) Write-Host "`n[$n] $t" -ForegroundColor Cyan }
function Write-OK    { param($m)    Write-Host "  OK    $m" -ForegroundColor Green }
function Write-Warn  { param($m)    Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Write-Err   { param($m)    Write-Host "  ERR   $m" -ForegroundColor Red }
function Write-Info  { param($m)    Write-Host "        $m" }

function Write-Banner {
    Write-Host ""
    Write-Host "=============================================================" -ForegroundColor Cyan
    Write-Host "  Enterprise RAG v2.0.8 - Windows PowerShell Startup" -ForegroundColor White
    Write-Host "  Dockerised : model-server, backend, open-webui, nginx" -ForegroundColor Gray
    Write-Host "  External   : SQL Server, PostgreSQL+pgvector (your servers)" -ForegroundColor Gray
    Write-Host "=============================================================" -ForegroundColor Cyan
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 3000)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $ar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $ar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($ok -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
    } catch {}
    return $false
}

function Get-EnvValue {
    param([string]$Key, [string]$Default = "")
    $line = Get-Content ".env" -ErrorAction SilentlyContinue |
            Where-Object { $_ -match "^\s*$Key\s*=" -and $_ -notmatch "^\s*#" } |
            Select-Object -First 1
    if ($line) {
        # Extract value part, then strip any trailing inline comment (# ...)
        $val = ($line -split "=", 2)[1]
        # Remove trailing comment: anything from " #" or "\t#" onwards
        $val = $val -replace "\s+#.*$", ""
        return $val.Trim()
    }
    return $Default
}

Write-Banner

# Stop mode
if ($Down) {
    Write-Host "`nStopping all services..." -ForegroundColor Yellow
    docker compose down
    Write-Host "Stopped. Data volumes preserved." -ForegroundColor Green
    exit 0
}

# STEP 1 - Docker Desktop
Write-Step "1/7" "Checking Docker Desktop..."
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Docker not running" }
    $dockerVer = docker version --format "{{.Server.Version}}" 2>$null
    Write-OK "Docker Engine $dockerVer"
} catch {
    Write-Err "Docker Desktop is not running or not installed."
    Write-Info "Install: https://www.docker.com/products/docker-desktop/"
    Read-Host "`nPress Enter to exit"
    exit 1
}

# STEP 2 - Docker Compose v2
Write-Step "2/7" "Checking Docker Compose v2..."
try {
    $null = docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Compose not found" }
    $composeVer = docker compose version 2>$null
    Write-OK "$composeVer"
} catch {
    Write-Err "Docker Compose v2 not found - update Docker Desktop."
    exit 1
}

# STEP 3 - .env file
Write-Step "3/7" "Checking .env configuration..."
if (-not (Test-Path ".env")) {
    if (-not (Test-Path ".env.example")) {
        Write-Err ".env.example not found. Run from the project root."
        exit 1
    }
    Copy-Item ".env.example" ".env"
    Write-Warn ".env created - ACTION REQUIRED"
    Write-Info ""
    Write-Info "Fill in these fields in .env:"
    Write-Info "  DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD"
    Write-Info "  PGVECTOR_HOST, PGVECTOR_PORT, PGVECTOR_DB, PGVECTOR_USER, PGVECTOR_PASSWORD"
    Write-Info "  LLM_TOKEN_URL, LLM_GENERATE_URL, LLM_USERNAME, LLM_PASSWORD"
    Write-Info "  OWUI_ADMIN_EMAIL, OWUI_ADMIN_PASSWORD, OWUI_SECRET_KEY, OWUI_AUTO_LOGIN_SECRET"

    # Auto-generate secrets if openssl is available
    $opensslCmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($opensslCmd) {
        $s1 = (openssl rand -hex 32 2>$null).Trim()
        $s2 = (openssl rand -hex 32 2>$null).Trim()
        if ($s1 -and $s2) {
            (Get-Content ".env") -replace "OWUI_SECRET_KEY=.*", "OWUI_SECRET_KEY=$s1" | Set-Content ".env"
            (Get-Content ".env") -replace "OWUI_AUTO_LOGIN_SECRET=.*", "OWUI_AUTO_LOGIN_SECRET=$s2" | Set-Content ".env"
            Write-OK "Random secrets generated for OWUI_SECRET_KEY and OWUI_AUTO_LOGIN_SECRET"
        }
    }

    $openEdit = Read-Host "`n  Open .env in Notepad? (Y/n)"
    if ($openEdit -notmatch "^[Nn]") {
        Start-Process notepad ".env" -Wait
    }
    $contSetup = Read-Host "  Continue startup? (Y/n)"
    if ($contSetup -match "^[Nn]") { exit 0 }
} else {
    Write-OK ".env file exists"
}

# STEP 4 - External DB connectivity
Write-Step "4/7" "Checking external database connectivity..."
Write-Info ""

$DbHost = Get-EnvValue "DB_HOST"
$DbPort = [int](Get-EnvValue "DB_PORT" "1433")
$PgHost = Get-EnvValue "PGVECTOR_HOST"
$PgPort = [int](Get-EnvValue "PGVECTOR_PORT" "5432")

# SQL Server check
if (-not $DbHost -or $DbHost -match "your-" -or $DbHost -eq "192.168.1.100") {
    Write-Warn "DB_HOST not configured in .env - skipping SQL Server check"
} else {
    Write-Info "  SQL Server : ${DbHost}:${DbPort}"
    if (Test-TcpPort -HostName $DbHost -Port $DbPort) {
        Write-OK "SQL Server reachable at ${DbHost}:${DbPort}"
    } else {
        Write-Err "Cannot reach SQL Server at ${DbHost}:${DbPort}"
        Write-Info ""
        Write-Info "  Check:"
        Write-Info "    1. DB_HOST and DB_PORT in .env are correct"
        Write-Info "    2. SQL Server TCP/IP is enabled (SQL Server Configuration Manager)"
        Write-Info "    3. Firewall allows port $DbPort from this machine"
        Write-Info "    4. Run SETUP-DB.bat to create the EnterpriseRAG database and schema"
        Write-Info ""
        $contSql = Read-Host "  Continue anyway? (Y/n)"
        if ($contSql -match "^[Nn]") { exit 1 }
    }
}

Write-Info ""

# PostgreSQL check
if (-not $PgHost -or $PgHost -match "your-" -or $PgHost -eq "192.168.1.101") {
    Write-Warn "PGVECTOR_HOST not configured in .env - skipping PostgreSQL check"
} else {
    Write-Info "  PostgreSQL : ${PgHost}:${PgPort}"
    if (Test-TcpPort -HostName $PgHost -Port $PgPort) {
        Write-OK "PostgreSQL reachable at ${PgHost}:${PgPort}"
    } else {
        Write-Err "Cannot reach PostgreSQL at ${PgHost}:${PgPort}"
        Write-Info ""
        Write-Info "  Check:"
        Write-Info "    1. PGVECTOR_HOST and PGVECTOR_PORT in .env are correct"
        Write-Info "    2. PostgreSQL is running and listening on that address"
        Write-Info "    3. pg_hba.conf allows connections from this machine"
        Write-Info "    4. Run SETUP-DB.bat to create the rag_vectors database and schema"
        Write-Info ""
        $contPg = Read-Host "  Continue anyway? (Y/n)"
        if ($contPg -match "^[Nn]") { exit 1 }
    }
}

# STEP 5 - Auth mode
Write-Step "5/7" "Checking authentication mode..."
$DevMode = Get-EnvValue "APP_DEV_MODE" "false"
if ($DevMode -eq "true") {
    Write-Warn "DEV MODE ON - JWT verification disabled. For local testing only."
} else {
    if (-not (Test-Path "backend\conf\jwt_public_key.pem")) {
        Write-Warn "JWT public key missing: backend\conf\jwt_public_key.pem"
        Write-Info "For dev testing: set APP_DEV_MODE=true in .env"
        Write-Info "For production, generate a key pair:"
        Write-Info "  openssl genrsa -out backend\conf\jwt_private_key.pem 2048"
        Write-Info "  openssl rsa -in backend\conf\jwt_private_key.pem -pubout -out backend\conf\jwt_public_key.pem"
        $contJwt = Read-Host "`n  Continue anyway? (Y/n)"
        if ($contJwt -match "^[Nn]") { exit 0 }
    } else {
        Write-OK "JWT public key found"
    }
}

# STEP 6 - Embedding model (auto-download from HuggingFace Hub)
Write-Step "6/7" "Embedding model..."
Write-OK "Model downloads automatically from HuggingFace Hub on first startup"
Write-Info "  Model  : sentence-transformers/all-MiniLM-L6-v2 (~90 MB)"
Write-Info "  No manual model loading required."
Write-Info "  First startup takes 2-5 extra minutes while downloading."
Write-Info "  Change model in backend/conf/app.properties (embedding.model_name)"

# STEP 7 - Build and start
Write-Step "7/7" "Building and starting Docker services..."
Write-Info "  Services: model-server, backend, open-webui, owui-auth-proxy, nginx"
Write-Info ""

if (-not $SkipBuild) {
    Write-Info "  Building images (first build: 5-15 min, cached: ~1-2 min)..."
    docker compose build
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Build failed. Check the output above for details."
        Read-Host "`nPress Enter to exit"
        exit 1
    }
    Write-OK "All images built successfully"
    Write-Info ""
}

docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to start one or more services."
    exit 1
}

# Wait for health
Write-Info ""
Write-Host "  Waiting for services to become healthy..." -ForegroundColor Cyan
Write-Info "  (Model-server takes up to 90 s on first boot while loading the model)"

$maxWait  = 150
$interval = 5
$elapsed  = 0
$ready    = $false

while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds $interval
    $elapsed += $interval
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost/api/v1/health/live" `
                                  -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # not ready yet
    }
    $pct = [math]::Round($elapsed * 100 / $maxWait)
    Write-Info "  ${elapsed}s / ${maxWait}s  ($pct%)"
}

Write-Host ""
docker compose ps
Write-Host ""

if ($ready) {
    Write-Host "=============================================================" -ForegroundColor Green
    Write-Host "  Enterprise RAG v2.0.8 is READY!" -ForegroundColor Green
    Write-Host "=============================================================" -ForegroundColor Green
} else {
    Write-Host "=============================================================" -ForegroundColor Yellow
    Write-Host "  Services started - some may still be initialising." -ForegroundColor Yellow
    Write-Host "  Watch: docker compose logs -f" -ForegroundColor Yellow
    Write-Host "=============================================================" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Access Points:" -ForegroundColor White
Write-Host "    Auto-login  : http://localhost/?token=<your_jwt>" -ForegroundColor Cyan
Write-Host "    Open WebUI  : http://localhost/" -ForegroundColor Cyan
Write-Host "    API Docs    : http://localhost/api/docs" -ForegroundColor Cyan
Write-Host "    Health      : http://localhost/api/v1/health" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Commands:" -ForegroundColor White
Write-Host "    All logs    : docker compose logs -f"
Write-Host "    Backend log : docker compose logs -f backend"
Write-Host "    Stop        : .\scripts\Stop-RAG.ps1"
Write-Host ""

if ($Logs) {
    Write-Host "  Tailing logs (Ctrl+C to stop)..." -ForegroundColor Cyan
    docker compose logs -f
} else {
    $openBrowser = Read-Host "Open http://localhost/api/docs in browser? (Y/n)"
    if ($openBrowser -notmatch "^[Nn]") {
        Start-Process "http://localhost/api/docs"
    }
}
