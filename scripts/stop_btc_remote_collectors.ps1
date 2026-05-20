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

function Stop-RecordedPid {
    param(
        [string]$Name,
        [int]$ProcessId,
        [string]$Source
    )
    if ($ProcessId -le 0) {
        return
    }
    if ($stopped | Where-Object { $_.process_id -eq $ProcessId }) {
        return
    }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $ProcessId -Force
        $script:stopped += [pscustomobject]@{
            name = $Name
            process_id = $ProcessId
            source = $Source
        }
    } else {
        $script:missing += [pscustomobject]@{
            name = $Name
            process_id = $ProcessId
            source = $Source
        }
    }
}

if (Test-Path -LiteralPath $pidDir) {
    foreach ($pidFile in Get-ChildItem -LiteralPath $pidDir -Filter "*.pid.json" -File -ErrorAction SilentlyContinue) {
        try {
            $pidInfo = Get-Content -LiteralPath $pidFile.FullName -Raw | ConvertFrom-Json
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
