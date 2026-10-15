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

    # Randomized production host ports (2026-10-15) - keep in sync with docker-compose.yml
    [int]$BackendPort = 24176,
    [int]$FrontendPort = 26816,

    [string]$RemoteHost = "production",
    [string]$RemoteUser = "ubuntu",
    [string]$RemoteDir = "/home/ubuntu/deployments/realtime-osint",
    [string]$TailscaleIP = ""
)

$ErrorActionPreference = "Stop"

# Load local .env overrides if present
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match '^\s*([^=]+?)\s*=\s*(.*)$') {
            $k = $matches[1].Trim()
            $v = $matches[2].Trim().Trim('"').Trim("'")
            if (-not [System.Environment]::GetEnvironmentVariable($k)) {
                [System.Environment]::SetEnvironmentVariable($k, $v)
            }
        }
    }
}

if (-not $PSBoundParameters.ContainsKey('TailscaleIP') -or [string]::IsNullOrWhiteSpace($TailscaleIP)) {
    if ($env:DEPLOY_TAILSCALE_IP) {
        $TailscaleIP = $env:DEPLOY_TAILSCALE_IP
    } elseif ($env:TAILSCALE_IP) {
        $TailscaleIP = $env:TAILSCALE_IP
    } else {
        $TailscaleIP = "127.0.0.1"
    }
}
if (-not $PSBoundParameters.ContainsKey('RemoteHost') -and $env:DEPLOY_REMOTE_HOST) {
    $RemoteHost = $env:DEPLOY_REMOTE_HOST
}
if (-not $PSBoundParameters.ContainsKey('RemoteUser') -and $env:DEPLOY_REMOTE_USER) {
    $RemoteUser = $env:DEPLOY_REMOTE_USER
}
if (-not $PSBoundParameters.ContainsKey('RemoteDir') -and $env:DEPLOY_REMOTE_DIR) {
    $RemoteDir = $env:DEPLOY_REMOTE_DIR
}

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

function Compress-Tar {
    # gzip a tar; returns $true on success. Compressing the image tar cuts the
    # Tailscale transfer by ~3x. Prefers fast native WSL gzip, falls back to
    # the slower built-in Windows bsdtar.
    param([string]$TarPath)
    $abs = [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $TarPath))
    try {
        $wslPath = (& wsl wslpath -a "$abs") | Select-Object -First 1
        if ($wslPath -and ($LASTEXITCODE -eq 0)) {
            & wsl gzip -1 -kf -- "$wslPath"
            if ($LASTEXITCODE -eq 0) { return $true }
        }
    } catch {}
    try {
        & tar -czf "$TarPath.gz" "$TarPath"
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$TarFilesToClean = @()

try {
    # Always keep configuration (.env and docker-compose.yml) in sync on production
    if (Test-Path .env) {
        Write-Step "Synchronizing .env configuration to $RemoteTarget..."
        scp .env "${RemoteTarget}:${RemoteDir}/.env"
        if ($LASTEXITCODE -ne 0) { throw "SCP failed for .env" }
        Write-Success ".env configuration synced to production."
    }

    # Always keep the remote compose file in sync when deploying the full stack or config.
    if ($Service -eq "all" -or $Service -eq "config") {
        Write-Step "Synchronizing docker-compose.yml to $RemoteTarget..."
        scp docker-compose.yml "${RemoteTarget}:${RemoteDir}/docker-compose.yml"
        if ($LASTEXITCODE -ne 0) { throw "SCP failed for docker-compose.yml" }
        Write-Success "docker-compose.yml synced (backend=${BackendPort}, frontend=${FrontendPort})."
    }

    if ($Service -eq "all" -or $Service -eq "backend") {
        Write-Step "Building backend image for linux/amd64..."
        Invoke-Docker @("build", "--platform", "linux/amd64", "-t", "realtimeosint-backend:latest", "./backend")
        Write-Success "Backend image built successfully."

        Write-Step "Exporting backend image to tar..."
        $backendTar = "backend_deploy.tar"
        Invoke-Docker @("save", "realtimeosint-backend:latest", "-o", $backendTar)
        $TarFilesToClean += $backendTar
        Write-Success "Exported $backendTar"

        $backendUpload = $backendTar
        $backendLoadCmd = "docker load -i $backendTar"
        if (Compress-Tar $backendTar) {
            $backendUpload = "$backendTar.gz"
            $TarFilesToClean += $backendUpload
            $backendLoadCmd = "gunzip -c $backendTar.gz | docker load"
            Write-Success "Compressed to $backendUpload ($([math]::Round((Get-Item $backendUpload).Length / 1MB)) MB)"
        }

        Write-Step "Uploading $backendUpload to ${RemoteTarget}:$RemoteDir..."
        scp $backendUpload "${RemoteTarget}:${RemoteDir}/$backendUpload"
        if ($LASTEXITCODE -ne 0) { throw "SCP failed for $backendUpload" }

        Write-Step "Loading backend image on remote server..."
        ssh $RemoteTarget "cd $RemoteDir && $backendLoadCmd && rm -f $backendTar $backendTar.gz && docker compose up -d backend"
        if ($LASTEXITCODE -ne 0) { throw "Remote backend deploy failed" }
        Write-Success "Backend updated and restarted on production."
    }

    if ($Service -eq "all" -or $Service -eq "frontend") {
        Write-Step "Building frontend image for linux/amd64..."
        Invoke-Docker @("build", "--platform", "linux/amd64", "-t", "realtimeosint-frontend:latest",
            "--build-arg", "NEXT_PUBLIC_API_PORT=$BackendPort",
            "--build-arg", "NEXT_PUBLIC_API_URL=http://localhost:8000",
            "--build-arg", "NEXT_PUBLIC_WS_URL=http://localhost:8000", "./frontend")
        Write-Success "Frontend image built successfully."

        Write-Step "Exporting frontend image to tar..."
        $frontendTar = "frontend_deploy.tar"
        Invoke-Docker @("save", "realtimeosint-frontend:latest", "-o", $frontendTar)
        $TarFilesToClean += $frontendTar
        Write-Success "Exported $frontendTar"

        $frontendUpload = $frontendTar
        $frontendLoadCmd = "docker load -i $frontendTar"
        if (Compress-Tar $frontendTar) {
            $frontendUpload = "$frontendTar.gz"
            $TarFilesToClean += $frontendUpload
            $frontendLoadCmd = "gunzip -c $frontendTar.gz | docker load"
            Write-Success "Compressed to $frontendUpload ($([math]::Round((Get-Item $frontendUpload).Length / 1MB)) MB)"
        }

        Write-Step "Uploading $frontendUpload to ${RemoteTarget}:$RemoteDir..."
        scp $frontendUpload "${RemoteTarget}:${RemoteDir}/$frontendUpload"
        if ($LASTEXITCODE -ne 0) { throw "SCP failed for $frontendUpload" }

        Write-Step "Loading frontend image on remote server..."
        ssh $RemoteTarget "cd $RemoteDir && $frontendLoadCmd && rm -f $frontendTar $frontendTar.gz && docker compose up -d frontend"
        if ($LASTEXITCODE -ne 0) { throw "Remote frontend deploy failed" }
        Write-Success "Frontend updated and restarted on production."
    }

    if ($Service -eq "all") {
        Write-Step "Reconciling full stack against synced compose..."
        ssh $RemoteTarget "cd $RemoteDir && docker compose up -d"
        if ($LASTEXITCODE -ne 0) { throw "Remote stack reconcile failed" }
        Write-Success "All services reconciled."
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
    $healthResp = Invoke-RestMethod -Uri "http://${TailscaleIP}:${BackendPort}/api/health" -TimeoutSec 10
    Write-Success "Backend API Health: $($healthResp.status) (Postgres: $($healthResp.database.status), Redis: $($healthResp.redis.status))"
} catch {
    Write-Warn "Could not query backend health endpoint: $_"
}

try {
    $frontendResp = Invoke-WebRequest -Uri "http://${TailscaleIP}:${FrontendPort}" -TimeoutSec 10 -UseBasicParsing
    Write-Success "Frontend Dashboard: HTTP $($frontendResp.StatusCode) OK"
} catch {
    Write-Warn "Could not query frontend endpoint: $_"
}

# Fetch remote resource stats
Write-Step "Remote Resource Utilization:"
ssh $RemoteTarget "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' && echo '' && free -h"

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE! All services operational." -ForegroundColor Green
Write-Host " Frontend: http://${RemoteHost}:${FrontendPort}  (or http://${TailscaleIP}:${FrontendPort})" -ForegroundColor Cyan
Write-Host " Backend:  http://${RemoteHost}:${BackendPort}  (or http://${TailscaleIP}:${BackendPort})" -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Green
