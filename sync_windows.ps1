# PineScript MCP Server - Git Sync for Windows (Native)
# Pull latest from GitHub and redeploy if changed
# Usage: Run manually or schedule via Task Scheduler

$ErrorActionPreference = "Stop"

$DeployDir = "C:\pinescript-mcp"
$LogFile = "$DeployDir\sync.log"
$Branch = "main"
$LocalFiles = @("daemon.sh", "sync.sh", "server.pid", "server.log", "sync.log", "supervisord.conf", "start.sh", "run.sh")

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Tee-Object -Append $LogFile
}

function Main {
    Log "=== Starting sync ==="

    Set-Location $DeployDir

    $Before = git rev-parse HEAD
    Log "Current HEAD: $Before"

    # Backup local-only files
    $BackupDir = "$env:TEMP\mcp_sync_backup"
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null

    foreach ($f in $LocalFiles) {
        if (Test-Path $f) {
            Copy-Item $f "$BackupDir\_$f" -Force
            Log "Backed up: $f"
        }
    }

    # Fetch and reset
    git fetch origin $Branch 2>&1 | ForEach-Object { Log "git: $_" }
    git reset --hard "origin/$Branch" 2>&1 | ForEach-Object { Log "git: $_" }

    $After = git rev-parse HEAD
    Log "New HEAD: $After"

    # Restore local files
    foreach ($f in $LocalFiles) {
        $backupPath = "$BackupDir\_$f"
        if (Test-Path $backupPath) {
            Copy-Item $backupPath $f -Force
            Remove-Item $backupPath
            Log "Restored: $f"
        }
    }

    if ($Before -eq $After) {
        Log "No changes, sync complete"
        return
    }

    Log "Changes detected: $Before -> $After"

    # Reinstall deps if requirements.txt changed
    $ChangedFiles = git diff --name-only $Before $After
    if ($ChangedFiles -contains "requirements.txt") {
        Log "requirements.txt changed - reinstalling dependencies"
        & "$DeployDir\.venv\Scripts\pip.exe" install -q -r requirements.txt 2>&1 | ForEach-Object { Log "pip: $_" }
    }

    # Restart the NSSM service
    Log "Restarting PineScript-MCP service..."
    $Service = Get-Service "PineScript-MCP" -ErrorAction SilentlyContinue
    if ($Service) {
        Restart-Service "PineScript-MCP"
        Log "Service restarted"
        Start-Sleep -Seconds 5

        # Verify health
        try {
            $Health = Invoke-RestMethod -Uri "http://localhost:8080/health" -TimeoutSec 5
            Log "Health check OK: $($Health | ConvertTo-Json -Compress)"
        }
        catch {
            Log "Health check failed: $_"
        }
    } else {
        Log "WARNING: PineScript-MCP service not found"
    }

    Log "=== Sync complete ($After) ==="
}

Main
