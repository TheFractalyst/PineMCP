# PineScript MCP Server - Health Check and Auto-Restart
# Run this via Task Scheduler every 5 minutes

$ErrorActionPreference = "Stop"

$ServiceName = "PineScript-MCP"
$LogFile = "C:\pinescript-mcp\health.log"
$MaxRetries = 3

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Tee-Object -Append $LogFile
}

# Check if service exists
$Service = Get-Service $ServiceName -ErrorAction SilentlyContinue
if (-not $Service) {
    Log "ERROR: Service $ServiceName not found"
    exit 1
}

# Check service status
if ($Service.Status -ne "Running") {
    Log "WARNING: Service is $($Service.Status), attempting to start..."
    try {
        Start-Service $ServiceName
        Start-Sleep -Seconds 5
        Log "Service started"
    }
    catch {
        Log "ERROR: Failed to start service: $_"
        exit 1
    }
}

# Health check HTTP endpoint
$Healthy = $false
for ($i = 0; $i -lt $MaxRetries; $i++) {
    try {
        $Health = Invoke-RestMethod -Uri "http://localhost:8080/health" -TimeoutSec 5
        Log "Health OK: status=$($Health.status), entries=$($Health.entries)"
        $Healthy = $true
        break
    }
    catch {
        Log "Health check attempt $($i+1) failed: $_"
        Start-Sleep -Seconds 2
    }
}

if (-not $Healthy) {
    Log "CRITICAL: Health check failed after $MaxRetries attempts, restarting service..."
    try {
        Restart-Service $ServiceName
        Start-Sleep -Seconds 10

        # Verify after restart
        $Health = Invoke-RestMethod -Uri "http://localhost:8080/health" -TimeoutSec 5
        Log "Service restarted, health: $($Health | ConvertTo-Json -Compress)"
    }
    catch {
        Log "ERROR: Failed to restart service: $_"
        exit 1
    }
}
