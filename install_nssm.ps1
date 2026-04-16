# PineScript MCP Server - NSSM Service Installer
# Run as Administrator

$ErrorActionPreference = "Stop"

$ServiceName = "PineScript-MCP"
$InstallDir = "C:\pinescript-mcp"
$PythonExe = "$InstallDir\.venv\Scripts\python.exe"
$ScriptPath = "$InstallDir\server.py"
$LogFile = "$InstallDir\service.log"

Write-Host "=== PineScript MCP NSSM Service Installer ===" -ForegroundColor Cyan
Write-Host ""

# Check if running as Administrator
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Must run as Administrator! Right-click PowerShell and select 'Run as Administrator'"
    exit 1
}

# Check if NSSM is available
$NSSM = Get-Command nssm.exe -ErrorAction SilentlyContinue
if (-not $NSSM) {
    Write-Host "NSSM not found in PATH. Downloading..." -ForegroundColor Yellow
    $NSSMUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $NSSMZip = "$env:TEMP\nssm.zip"
    $NSSMExtract = "$env:TEMP\nssm"

    try {
        Invoke-WebRequest -Uri $NSSMUrl -OutFile $NSSMZip -UseBasicParsing
        Expand-Archive -Path $NSSMZip -DestinationPath $NSSMExtract -Force

        # Find nssm.exe (could be in Win32 or x64 folder depending on architecture)
        $NSSMPath = Get-ChildItem -Path $NSSMExtract -Recurse -Filter "nssm.exe" | Select-Object -First 1
        if ($NSSMPath) {
            # Copy to a location in PATH
            $NSSMDest = "$InstallDir\nssm.exe"
            Copy-Item $NSSMPath.FullName $NSSMDest -Force
            $NSSM = Get-Command $NSSMDest
            Write-Host "NSSM installed to $NSSMDest" -ForegroundColor Green
        }
    }
    catch {
        Write-Error "Failed to download NSSM. Please download manually from https://nssm.cc/"
        exit 1
    }
}

Write-Host "Using NSSM: $($NSSM.Source)" -ForegroundColor Green

# Remove existing service
$ExistingService = Get-Service $ServiceName -ErrorAction SilentlyContinue
if ($ExistingService) {
    Write-Host "Removing existing service..." -ForegroundColor Yellow
    & $NSSM.Source stop $ServiceName 2>$null
    & $NSSM.Source remove $ServiceName confirm 2>$null
    Start-Sleep -Seconds 2
}

# Remove any existing Task Scheduler tasks (from previous attempts)
Get-ScheduledTask -TaskName "PineScript-MCP*" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

Write-Host "Installing service $ServiceName..." -ForegroundColor Cyan

# Install the service
& $NSSM.Source install $ServiceName $PythonExe
if ($LASTEXITCODE -ne 0) {
    Write-Error "NSSM install failed"
    exit 1
}

# Set the working directory
& $NSSM.Source set $ServiceName AppDirectory $InstallDir

# Set the arguments
& $NSSM.Source set $ServiceName AppParameters "-u server.py"

# Environment variables (space-separated KEY=VALUE pairs)
$EnvVars = @(
    "TRANSPORT=http",
    "HOST=0.0.0.0",
    "PORT=8080",
    "LAZY_MODEL=true",
    "PINESCRIPT_DB_PATH=C:\pinescript-mcp\pinescript_db",
    "PINESCRIPT_COLLECTION=pinescript_v6",
    "PINESCRIPT_EMBED_MODEL=all-MiniLM-L6-v2",
    "PINESCRIPT_MAX_RESULTS=20",
    "PINE_FACADE_TIMEOUT=20",
    "VALIDATION_CACHE_TTL=300",
    "LOG_LEVEL=INFO",
    "PYTHONUNBUFFERED=1",
    "PYTHONDONTWRITEBYTECODE=1"
) -join " "

& $NSSM.Source set $ServiceName AppEnvironmentExtra $EnvVars

# Logging configuration
& $NSSM.Source set $ServiceName AppStdout $LogFile
& $NSSM.Source set $ServiceName AppStderr $LogFile
& $NSSM.Source set $ServiceName AppStdoutCreationDisposition 2  # Append
& $NSSM.Source set $ServiceName AppStderrCreationDisposition 2  # Append

# Auto-start on boot, restart on failure
& $NSSM.Source set $ServiceName Start SERVICE_AUTO_START
& $NSSM.Source set $ServiceName AppRestartDelay 5000  # 5 seconds

# Graceful shutdown
& $NSSM.Source set $ServiceName AppThrottle 30000  # 30 sec startup timeout
& $NSSM.Source set $ServiceName AppStopMethodSkip 6  # Skip all except Ctrl+C

# Start the service
Write-Host "Starting service..." -ForegroundColor Cyan
Start-Service $ServiceName
Start-Sleep -Seconds 3

# Check status
$Service = Get-Service $ServiceName
Write-Host ""
Write-Host "=== Service Status ===" -ForegroundColor Green
Write-Host "Name: $($Service.Name)"
Write-Host "Status: $($Service.Status)"
Write-Host "StartType: $($Service.StartType)"
Write-Host ""
Write-Host "Log file: $LogFile"
Write-Host ""

if ($Service.Status -eq "Running") {
    Write-Host "Service installed and running!" -ForegroundColor Green

    # Test health endpoint
    Start-Sleep -Seconds 5
    try {
        $Health = Invoke-RestMethod -Uri "http://localhost:8080/health" -TimeoutSec 5
        Write-Host "Health check: $($Health | ConvertTo-Json)" -ForegroundColor Green
    }
    catch {
        Write-Host "Health check failed (may need more time to start): $_" -ForegroundColor Yellow
    }
}
else {
    Write-Error "Service not running. Check logs: $LogFile"
    Write-Host "NSSM output:" -ForegroundColor Yellow
    & $NSSM.Source dump $ServiceName
    exit 1
}

Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  nssm status $ServiceName    - Check service status"
Write-Host "  nssm restart $ServiceName   - Restart service"
Write-Host "  nssm stop $ServiceName      - Stop service"
Write-Host "  nssm start $ServiceName     - Start service"
Write-Host "  nssm edit $ServiceName      - Edit service configuration"
