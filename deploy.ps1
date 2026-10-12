<#
.SYNOPSIS
    Automated Deployment Workflow for Realtime OSINT -> ubuntu@production

.DESCRIPTION
    Builds optimized linux/amd64 images locally, uploads them to the production server via SSH/SCP,
    updates the running Docker stack without port clashes, and verifies health checks.

.PARAMETER Service
    Target service to build and deploy: 'all' (default), 'frontend', 'backend', or 'config'.

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -Service frontend
    .\deploy.ps1 -Service backend
    .\deploy.ps1 -Service config
#>

param(
    [ValidateSet("all", "frontend", "backend", "config")]
    [string]$Service = "all",

    [string]$RemoteHost = "production",
    [string]$RemoteUser = "ubuntu",
    [string]$RemoteDir = "/home/ubuntu/deployments/realtime-osint",
    [string]$TailscaleIP = "100.64.0.1"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Text)
    Write-Host "`n====> $Text" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Text)
    Write-Host " [OK] $Text" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Text)
    Write-Host " [WARN] $Text" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Text)
    Write-Host " [ERR] $Text" -ForegroundColor Red
}

$RemoteTarget = "$RemoteUser@$RemoteHost"

Write-Step "Checking SSH connection to $RemoteTarget..."
try {
    $sshTest = ssh -o BatchMode=yes -o ConnectTimeout=5 $RemoteTarget "echo ping" 2>&1
    if ($sshTest -notmatch "ping") {
        throw "Failed to ping remote host: $sshTest"
    }
    Write-Success "SSH connection established to $RemoteTarget"
} catch {
    Write-Err "Could not connect to $RemoteTarget over SSH. Ensure Tailscale is running and $RemoteTarget is reachable."
    exit 1
}

# Determine Docker CLI engine (WSL or Native Windows)
Write-Step "Detecting local Docker engine..."
$UseWsl = $false
try {
    $nativeTest = docker info --format '{{.ServerVersion}}' 2>$null
    if ($nativeTest) {
        Write-Success "Using native Docker engine ($nativeTest)"
    }
} catch {}

if (-not $nativeTest) {
    try {
        $wslTest = wsl docker info --format '{{.ServerVersion}}' 2>$null
        if ($wslTest) {
            $UseWsl = $true
            Write-Success "Using WSL Docker engine ($wslTest)"
        }
    } catch {}
}

if (-not $nativeTest -and -not $UseWsl) {
    Write-Err "Neither native Docker nor WSL Docker is active. Please start Docker Desktop or WSL Docker daemon."
    exit 1
}

function Invoke-Docker {
    param([string[]]$CommandArgs)
    if ($UseWsl) {
        & wsl docker @CommandArgs
    } else {
        & docker @CommandArgs
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: docker $($CommandArgs -join ' ')"
    }
}

$TarFilesToClean = @()

try {
    if ($Service -eq "all" -or $Service -eq "backend") {
        Write-Step "Building backend image for linux/amd64..."
        Invoke-Docker @("build", "--platform", "linux/amd64", "-t", "realtimeosint-backend:latest", "./backend")
        Write-Success "Backend image built successfully."

        Write-Step "Exporting backend image to tar..."
        $backendTar = "backend_deploy.tar"
        Invoke-Docker @("save", "realtimeosint-backend:latest", "-o", $backendTar)
        $TarFilesToClean += $backendTar
        Write-Success "Exported $backendTar"

        Write-Step "Uploading $backendTar to ${RemoteTarget}:$RemoteDir..."
        scp $backendTar "${RemoteTarget}:${RemoteDir}/$backendTar"
        if ($LASTEXITCODE -ne 0) { throw "SCP failed for $backendTar" }

        Write-Step "Loading backend image on remote server..."
        ssh $RemoteTarget "cd $RemoteDir && docker load -i $backendTar && rm -f $backendTar && docker compose up -d backend"
        if ($LASTEXITCODE -ne 0) { throw "Remote backend deploy failed" }
        Write-Success "Backend updated and restarted on production."
    }

    if ($Service -eq "all" -or $Service -eq "frontend") {
        Write-Step "Building frontend image for linux/amd64..."
        Invoke-Docker @("build", "--platform", "linux/amd64", "-t", "realtimeosint-frontend:latest", "./frontend")
        Write-Success "Frontend image built successfully."

        Write-Step "Exporting frontend image to tar..."
        $frontendTar = "frontend_deploy.tar"
        Invoke-Docker @("save", "realtimeosint-frontend:latest", "-o", $frontendTar)
        $TarFilesToClean += $frontendTar
        Write-Success "Exported $frontendTar"

        Write-Step "Uploading $frontendTar to ${RemoteTarget}:$RemoteDir..."
        scp $frontendTar "${RemoteTarget}:${RemoteDir}/$frontendTar"
        if ($LASTEXITCODE -ne 0) { throw "SCP failed for $frontendTar" }

        Write-Step "Loading frontend image on remote server..."
        ssh $RemoteTarget "cd $RemoteDir && docker load -i $frontendTar && rm -f $frontendTar && docker compose up -d frontend"
        if ($LASTEXITCODE -ne 0) { throw "Remote frontend deploy failed" }
        Write-Success "Frontend updated and restarted on production."
    }

    if ($Service -eq "config") {
        Write-Step "Synchronizing compose configuration to remote..."
        ssh $RemoteTarget "cd $RemoteDir && docker compose up -d"
        Write-Success "Containers reconciled against current config."
    }

} finally {
    # Clean up local temporary tar files
    foreach ($tar in $TarFilesToClean) {
        if (Test-Path $tar) {
            Remove-Item -Force $tar
        }
    }
}

# Verify Health Checks
Write-Step "Verifying remote health checks..."
Start-Sleep -Seconds 2

try {
    $healthResp = Invoke-RestMethod -Uri "http://${TailscaleIP}:8080/api/health" -TimeoutSec 10
    Write-Success "Backend API Health: $($healthResp.status) (Postgres: $($healthResp.database.status), Redis: $($healthResp.redis.status))"
} catch {
    Write-Warn "Could not query backend health endpoint: $_"
}

try {
    $frontendResp = Invoke-WebRequest -Uri "http://${TailscaleIP}:3000" -TimeoutSec 10 -UseBasicParsing
    Write-Success "Frontend Dashboard: HTTP $($frontendResp.StatusCode) OK"
} catch {
    Write-Warn "Could not query frontend endpoint: $_"
}

# Fetch remote resource stats
Write-Step "Remote Resource Utilization:"
ssh $RemoteTarget "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' && echo '' && free -h"

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE! All services operational." -ForegroundColor Green
Write-Host " Frontend: http://${RemoteHost}:3000  (or http://${TailscaleIP}:3000)" -ForegroundColor Cyan
Write-Host " Backend:  http://${RemoteHost}:8080  (or http://${TailscaleIP}:8080)" -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Green
