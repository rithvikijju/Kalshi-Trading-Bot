param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$MaxStatusAgeSec = 900,
    [int]$StartupGraceSec = 300,
    [switch]$InstallWatchdog,
    [string]$WatchdogUser = "",
    [string]$WatchdogPassword = ""
)

$ErrorActionPreference = "Stop"

function Get-StatusObject {
    param([string]$RepoRoot)
    $statusText = & (Join-Path $PSScriptRoot "status_btc_remote_collectors.ps1") -RepoRoot $RepoRoot | Out-String
    if (!$statusText.Trim()) {
        return $null
    }
    return $statusText | ConvertFrom-Json
}

function Read-StatusSidecar {
    param([string]$Path)
    if (!(Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            path = $Path
            exists = $false
            fresh = $false
            age_sec = $null
            dropped = $null
            queue_depth = $null
            error = "missing"
        }
    }
    try {
        $obj = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $updated = [datetimeoffset]::Parse([string]$obj.updated_at_utc).UtcDateTime
        $age = ((Get-Date).ToUniversalTime() - $updated).TotalSeconds
        return [pscustomobject]@{
            path = $Path
            exists = $true
            fresh = ($age -le $MaxStatusAgeSec)
            age_sec = [math]::Round($age, 3)
            dropped = $obj.dropped
            queue_depth = $obj.queue_depth
            error = ""
        }
    } catch {
        $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
        $age = if ($item) { ((Get-Date).ToUniversalTime() - $item.LastWriteTimeUtc).TotalSeconds } else { $null }
        return [pscustomobject]@{
            path = $Path
            exists = $true
            fresh = ($age -ne $null -and $age -le $MaxStatusAgeSec)
            age_sec = if ($age -ne $null) { [math]::Round($age, 3) } else { $null }
            dropped = $null
            queue_depth = $null
            error = $_.Exception.Message
        }
    }
}

function Install-WatchdogTask {
    param(
        [string]$RepoRoot,
        [string]$WatchdogUser,
        [string]$WatchdogPassword
    )
    $taskName = "KalshiBTC_collector_watchdog"
    $scriptPath = Join-Path $PSScriptRoot "ensure_btc_remote_collectors.ps1"
    $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -RepoRoot `"$RepoRoot`""
    $args = "/Create /TN `"$taskName`" /SC MINUTE /MO 5 /TR `"$taskCommand`" /F"
    if ($WatchdogUser) {
        $args += " /RU `"$WatchdogUser`""
    }
    if ($WatchdogPassword) {
        $args += " /RP `"$WatchdogPassword`""
    }
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = cmd.exe /c "schtasks $args" 2>&1
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create watchdog task $taskName`: $output"
    }
    try {
        if (Get-Command New-ScheduledTaskSettingsSet -ErrorAction SilentlyContinue) {
            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
                -MultipleInstances IgnoreNew
            Set-ScheduledTask -TaskName $taskName -Settings $settings | Out-Null
        }
    } catch {
        Write-Warning "Failed to relax watchdog scheduled-task settings: $($_.Exception.Message)"
    }
    return [pscustomobject]@{
        task_name = $taskName
        installed = $true
        output = ($output -join "`n")
    }
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$runtimeDir = Join-Path $RepoRoot "runtime"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$watchdog = $null
if ($InstallWatchdog) {
    $watchdog = Install-WatchdogTask -RepoRoot $RepoRoot -WatchdogUser $WatchdogUser -WatchdogPassword $WatchdogPassword
}

$status = $null
try {
    $status = Get-StatusObject -RepoRoot $RepoRoot
} catch {
    $status = [pscustomobject]@{
        status = "STATUS_ERROR"
        error = $_.Exception.Message
        rows = @()
    }
}

$statusPaths = @(
    (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc15m_live_capture.duckdb.status.json"),
    (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc15m_lowdd_forward_shadow_capture.duckdb.status.json")
)
$sidecars = @($statusPaths | ForEach-Object { Read-StatusSidecar -Path $_ })
$hasCurrentActiveStatus = $status -and ($status.PSObject.Properties.Name -contains "current_active_status")
$allProcessesRunning = if ($hasCurrentActiveStatus) {
    $status.current_active_status -eq "ALL_CURRENT_ACTIVE_RUNNING"
} else {
    $status -and $status.status -eq "ALL_RUNNING"
}
$allSidecarsFresh = ($sidecars | Where-Object { $_.fresh }).Count -eq $sidecars.Count
$unsafeSupervisionRows = @()
if ($status -and $status.rows) {
    $unsafeSupervisionRows = @(
        $status.rows | Where-Object {
            [string]$_.task_supervision_status -eq "PROCESS_RUNNING_TASK_NOT_RUNNING"
        }
    )
}
$hasProcessRunningTaskNotRunning = $unsafeSupervisionRows.Count -gt 0
$allProcessesInStartupGrace = $false
if ($allProcessesRunning -and $status.rows) {
    $runningRows = @($status.rows)
    $recentRows = @(
        $runningRows | Where-Object {
            try {
                $age = ((Get-Date).ToUniversalTime() - [datetimeoffset]::Parse([string]$_.start_time).UtcDateTime).TotalSeconds
                $age -ge 0 -and $age -le $StartupGraceSec
            } catch {
                $false
            }
        }
    )
    $allProcessesInStartupGrace = ($runningRows.Count -gt 0 -and $recentRows.Count -eq $runningRows.Count)
}

$action = "noop"
$startResult = $null
$restartBlockedReason = ""
if ($hasProcessRunningTaskNotRunning) {
    $action = "blocked_process_running_task_not_running"
    $restartBlockedReason = (
        "One or more collectors are live but their scheduled task is not running; " +
        "not restarting because that could create duplicate writers against a locked DB."
    )
} elseif ($hasCurrentActiveStatus -and (!$allProcessesRunning -or (!$allSidecarsFresh -and !$allProcessesInStartupGrace))) {
    $action = "restart_current_active_collectors"
    $startResult = & (Join-Path $PSScriptRoot "start_btc_remote_collectors.ps1") -RepoRoot $RepoRoot | Out-String
    Start-Sleep -Seconds 10
    $status = Get-StatusObject -RepoRoot $RepoRoot
    $sidecars = @($statusPaths | ForEach-Object { Read-StatusSidecar -Path $_ })
} elseif (!$allProcessesRunning -or (!$allSidecarsFresh -and !$allProcessesInStartupGrace)) {
    $action = "restart_collectors"
    $startResult = & (Join-Path $PSScriptRoot "start_btc_remote_collectors.ps1") -RepoRoot $RepoRoot | Out-String
    Start-Sleep -Seconds 10
    $status = Get-StatusObject -RepoRoot $RepoRoot
    $sidecars = @($statusPaths | ForEach-Object { Read-StatusSidecar -Path $_ })
}

$result = [pscustomobject]@{
    repo_root = $RepoRoot
    checked_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    action = $action
    watchdog = $watchdog
    process_status = $status
    sidecar_status = $sidecars
    restart_blocked_reason = $restartBlockedReason
    unsafe_supervision_rows = $unsafeSupervisionRows
    start_result = $startResult
}
$resultPath = Join-Path $runtimeDir "btc_collectors_ensure_latest.json"
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resultPath -Encoding UTF8
$result | ConvertTo-Json -Depth 8
