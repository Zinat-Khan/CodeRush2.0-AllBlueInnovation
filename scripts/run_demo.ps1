<# 
  AE-03 Orchestrator — Single-Command Demo Launcher
  
  Usage:
    .\scripts\run_demo.ps1
  
  Starts both the FastAPI backend (port 8000) and Next.js frontend (port 3000),
  waits for health checks, then prints the dashboard URL.
#>

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  AE-03 Orchestrator — Demo Launcher" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$projectRoot = Split-Path -Parent $PSScriptRoot

# ── Start Backend ──────────────────────────────────────────────────────
Write-Host "[1/4] Starting FastAPI backend on port 8000..." -ForegroundColor Yellow

$backendJob = Start-Job -ScriptBlock {
    param($root)
    Set-Location $root
    & python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload 2>&1
} -ArgumentList $projectRoot

Write-Host "       Backend started (Job ID: $($backendJob.Id))" -ForegroundColor DarkGray

# ── Start Frontend ─────────────────────────────────────────────────────
Write-Host "[2/4] Starting Next.js frontend on port 3000..." -ForegroundColor Yellow

$frontendJob = Start-Job -ScriptBlock {
    param($root)
    Set-Location "$root\frontend"
    & npm run dev -- -p 3000 2>&1
} -ArgumentList $projectRoot

Write-Host "       Frontend started (Job ID: $($frontendJob.Id))" -ForegroundColor DarkGray

# ── Health Checks ──────────────────────────────────────────────────────
Write-Host "[3/4] Waiting for services to be ready..." -ForegroundColor Yellow

$backendReady = $false
$frontendReady = $false
$maxAttempts = 30
$attempt = 0

while ($attempt -lt $maxAttempts -and (-not $backendReady -or -not $frontendReady)) {
    Start-Sleep -Seconds 1
    $attempt++

    if (-not $backendReady) {
        try {
            $response = Invoke-RestMethod -Uri "http://localhost:8000/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($response.status -eq "healthy") {
                $backendReady = $true
                Write-Host "       Backend ready" -ForegroundColor Green
            }
        } catch { }
    }

    if (-not $frontendReady) {
        try {
            $null = Invoke-WebRequest -Uri "http://localhost:3000" -TimeoutSec 2 -ErrorAction SilentlyContinue
            $frontendReady = $true
            Write-Host "       Frontend ready" -ForegroundColor Green
        } catch { }
    }
}

if (-not $backendReady) {
    Write-Host "       WARNING: Backend may not be ready (check logs)" -ForegroundColor Red
}
if (-not $frontendReady) {
    Write-Host "       WARNING: Frontend may not be ready (check logs)" -ForegroundColor Red
}

# ── Print Dashboard URL ───────────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] Services launched!" -ForegroundColor Green
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Dashboard:  http://localhost:3000" -ForegroundColor White
Write-Host "  API Docs:   http://localhost:8000/api/docs" -ForegroundColor White
Write-Host "  Health:     http://localhost:8000/api/health" -ForegroundColor White
Write-Host "  SSE Demo:   http://localhost:8000/api/sse/demo" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop both services." -ForegroundColor DarkGray
Write-Host ""

# ── Keep alive and relay logs ──────────────────────────────────────────
try {
    while ($true) {
        # Check if jobs are still running
        $bState = (Get-Job -Id $backendJob.Id).State
        $fState = (Get-Job -Id $frontendJob.Id).State

        # Print any new output
        Receive-Job -Id $backendJob.Id -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "[BE] $_" -ForegroundColor DarkGray
        }
        Receive-Job -Id $frontendJob.Id -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "[FE] $_" -ForegroundColor DarkGray
        }

        if ($bState -eq "Failed" -or $fState -eq "Failed") {
            Write-Host "A service has stopped unexpectedly." -ForegroundColor Red
            break
        }

        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host ""
    Write-Host "Stopping services..." -ForegroundColor Yellow
    Stop-Job -Id $backendJob.Id -ErrorAction SilentlyContinue
    Stop-Job -Id $frontendJob.Id -ErrorAction SilentlyContinue
    Remove-Job -Id $backendJob.Id -ErrorAction SilentlyContinue
    Remove-Job -Id $frontendJob.Id -ErrorAction SilentlyContinue
    Write-Host "Done." -ForegroundColor Green
}
