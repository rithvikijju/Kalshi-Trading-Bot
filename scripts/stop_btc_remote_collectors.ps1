param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$runtimeDir = Join-Path $RepoRoot "runtime"
$manifestPath = Join-Path $runtimeDir "btc_collectors_processes.json"
$pidDir = Join-Path $runtimeDir "pids"
$stopped = @()
$missing = @()

function Get-CaptureStatusPaths {
    param([string]$RepoRoot)
    return @(
        (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc15m_live_capture.duckdb.status.json"),
        (Join-Path $RepoRoot ".codex_work\btc15m_f2_q250_qty500_firstskip_shadow\btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb.status.json"),
        (Join-Path $RepoRoot ".codex_work\btc15m_f2_q250_qty500_firstskip_yes_shadow\btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb.status.json"),
        (Join-Path $RepoRoot ".codex_work\btc15m_f2_q1000_yes_shadow\btc15m_f2_q1000_yes_shadow_capture.duckdb.status.json"),
        (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb.status.json")
    )
}

function Stop-CaptureSidecarPids {
    param([string]$RepoRoot)
    foreach ($statusPath in (Get-CaptureStatusPaths -RepoRoot $RepoRoot)) {
        if (!(Test-Path -LiteralPath $statusPath)) {
            continue
        }
        try {
            $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        } catch {
            continue
        }
        $name = Split-Path -Leaf $statusPath
        if ($status.pid) {
            Stop-RecordedPid -Name $name -ProcessId ([int]$status.pid) -Source "capture_status_sidecar_pid"
        }
        $lastError = [string]$status.last_error
        if ($lastError) {
            foreach ($match in [regex]::Matches($lastError, 'PID\s+(\d+)')) {
                Stop-RecordedPid -Name $name -ProcessId ([int]$match.Groups[1].Value) -Source "capture_status_duckdb_lock_holder"
            }
        }
    }
}

function Stop-RecordedPid {
    param(
        [string]$Name,
        [int]$ProcessId,
        [string]$Source
    )
    if ($ProcessId -le 0) {
        return
    }
    if ($ProcessId -eq $PID) {
        $script:missing += [pscustomobject]@{
            name = $Name
            process_id = $ProcessId
            source = "$Source`_self_pid_skip"
            process_name = "powershell"
        }
        return
    }
    if ($stopped | Where-Object { $_.process_id -eq $ProcessId }) {
        return
    }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($proc) {
        $expectedLauncher = ($Name -like "*_launcher" -or $Source -like "*launcher*")
        $procName = [string]$proc.ProcessName
        $expected = if ($expectedLauncher) {
            $procName -in @("powershell", "pwsh")
        } else {
            $procName -like "python*"
        }
        if (!$expected) {
            $script:missing += [pscustomobject]@{
                name = $Name
                process_id = $ProcessId
                source = "$Source`_reused_pid_skip"
                process_name = $procName
            }
            return
        }
        try {
            Stop-Process -Id $ProcessId -Force -ErrorAction Stop
            $script:stopped += [pscustomobject]@{
                name = $Name
                process_id = $ProcessId
                source = $Source
                process_name = $procName
            }
        } catch {
            $script:missing += [pscustomobject]@{
                name = $Name
                process_id = $ProcessId
                source = "$Source`_stop_failed"
                process_name = $procName
                error = $_.Exception.Message
            }
        }
    } else {
        $script:missing += [pscustomobject]@{
            name = $Name
            process_id = $ProcessId
            source = $Source
        }
    }
}

Stop-CaptureSidecarPids -RepoRoot $RepoRoot

if (Test-Path -LiteralPath $pidDir) {
    $latestPidFiles = @(
        "btc15m_live_capture.pid.json",
        "btc15m_q250_qty500_firstskip_shadow.pid.json",
        "btc15m_q250_qty500_firstskip_yes_shadow.pid.json",
        "btc15m_q1000_yes_shadow.pid.json",
        "btc1h_high_conf80_entry70_no_chase_shadow.pid.json"
    )
    foreach ($pidFileName in $latestPidFiles) {
        $pidFilePath = Join-Path $pidDir $pidFileName
        if (!(Test-Path -LiteralPath $pidFilePath)) {
            continue
        }
        try {
            $pidInfo = Get-Content -LiteralPath $pidFilePath -Raw | ConvertFrom-Json
            Stop-RecordedPid -Name ([string]$pidInfo.name) -ProcessId ([int]$pidInfo.child_process_id) -Source "pid_sidecar_child"
            Stop-RecordedPid -Name ("$($pidInfo.name)_launcher") -ProcessId ([int]$pidInfo.launcher_pid) -Source "pid_sidecar_launcher"
        } catch {
        }
    }
}

if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($row in @($manifest.started)) {
        $targetPid = [int]$row.process_id
        Stop-RecordedPid -Name ([string]$row.name) -ProcessId $targetPid -Source "manifest"
        if ($row.launcher_pid) {
            $launcherPid = [int]$row.launcher_pid
            Stop-RecordedPid -Name ("$($row.name)_launcher") -ProcessId $launcherPid -Source "manifest_launcher"
        }
        if ($row.task_name) {
            $taskName = [string]$row.task_name
            cmd.exe /c "schtasks /End /TN `"$taskName`" >nul 2>nul" | Out-Null
            cmd.exe /c "schtasks /Delete /TN `"$taskName`" /F >nul 2>nul" | Out-Null
        }
    }
}

foreach ($taskName in @(
    "KalshiBTC_btc15m_live_capture",
    "KalshiBTC_btc15m_q250_qty500_firstskip_shadow",
    "KalshiBTC_btc15m_q250_qty500_firstskip_yes_shadow",
    "KalshiBTC_btc15m_q1000_yes_shadow",
    "KalshiBTC_btc1h_high_conf80_entry70_no_chase_shadow"
)) {
    cmd.exe /c "schtasks /End /TN `"$taskName`" >nul 2>nul" | Out-Null
    cmd.exe /c "schtasks /Delete /TN `"$taskName`" /F >nul 2>nul" | Out-Null
}

# Best-effort exact script sweep. On some locked-down Windows accounts CIM is
# denied; in that case we rely only on the manifest PIDs and do not broaden.
$exactScripts = @(
    "btc15m_live_capture.py",
    "btc15m_f2_q250_qty500_firstskip_shadow.py",
    "btc15m_f2_q250_qty500_firstskip_yes_shadow.py",
    "btc15m_f2_q1000_yes_shadow.py",
    "btc_1hr_high_conf80_entry70_no_chase_shadow.py"
)
$sweepError = ""
try {
    $pythonProcesses = Get-CimInstance Win32_Process -Filter "name = 'python.exe'"
    foreach ($process in $pythonProcesses) {
        $cmd = [string]$process.CommandLine
        if (!$cmd) {
            continue
        }
        foreach ($script in $exactScripts) {
            if ($cmd -like "*$script*") {
                $targetPid = [int]$process.ProcessId
                if (!($stopped | Where-Object { $_.process_id -eq $targetPid })) {
                    Stop-Process -Id $targetPid -Force
                    $stopped += [pscustomobject]@{
                        name = $script
                        process_id = $targetPid
                        source = "exact_script_sweep"
                    }
                }
            }
        }
    }
} catch {
    $sweepError = $_.Exception.Message
}

$result = [pscustomobject]@{
    repo_root = $RepoRoot
    stopped_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    stopped = $stopped
    missing = $missing
    exact_script_sweep_error = $sweepError
}

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$resultPath = Join-Path $runtimeDir ("btc_collectors_stop_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultPath -Encoding UTF8
if (!$Quiet) {
    $result | ConvertTo-Json -Depth 5
}
