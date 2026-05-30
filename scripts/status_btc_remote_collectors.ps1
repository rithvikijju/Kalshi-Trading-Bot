param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$runtimeDir = Join-Path $RepoRoot "runtime"
$manifestPath = Join-Path $runtimeDir "btc_collectors_processes.json"

if (!(Test-Path -LiteralPath $manifestPath)) {
    [pscustomobject]@{
        repo_root = $RepoRoot
        status = "NO_MANIFEST"
        manifest = $manifestPath
        rows = @()
    } | ConvertTo-Json -Depth 5
    exit 1
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$rows = @()

function Get-CaptureStatusPath {
    param(
        [string]$RepoRoot,
        [string]$Name
    )
    switch ($Name) {
        "btc15m_live_capture" { return (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc15m_live_capture.duckdb.status.json") }
        "btc15m_q250_qty500_firstskip_shadow" { return (Join-Path $RepoRoot ".codex_work\btc15m_f2_q250_qty500_firstskip_shadow\btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb.status.json") }
        "btc15m_q250_qty500_firstskip_yes_shadow" { return (Join-Path $RepoRoot ".codex_work\btc15m_f2_q250_qty500_firstskip_yes_shadow\btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb.status.json") }
        "btc15m_q1000_yes_shadow" { return (Join-Path $RepoRoot ".codex_work\btc15m_f2_q1000_yes_shadow\btc15m_f2_q1000_yes_shadow_capture.duckdb.status.json") }
        "btc1h_high_conf80_entry70_no_chase_shadow" { return (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb.status.json") }
        default { return "" }
    }
}

function Read-CaptureStatusPid {
    param([string]$Path)
    if (!$Path -or !(Test-Path -LiteralPath $Path)) {
        return 0
    }
    try {
        $obj = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        return [int]$obj.pid
    } catch {
        return 0
    }
}

foreach ($row in @($manifest.started)) {
    $manifestPid = [int]$row.process_id
    $targetPid = $manifestPid
    $pidSource = "manifest"
    $statusPid = Read-CaptureStatusPid -Path (Get-CaptureStatusPath -RepoRoot $RepoRoot -Name ([string]$row.name))
    if ($statusPid -gt 0) {
        $statusProc = Get-Process -Id $statusPid -ErrorAction SilentlyContinue
        if ($statusProc -and ([string]$statusProc.ProcessName) -like "python*") {
            $targetPid = $statusPid
            $pidSource = "capture_status_sidecar"
        }
    }
    $proc = if ($targetPid -gt 0) { Get-Process -Id $targetPid -ErrorAction SilentlyContinue } else { $null }
    $processName = if ($proc) { [string]$proc.ProcessName } else { "" }
    $expectedProcess = if ($proc) { $processName -like "python*" } else { $false }
    $startTime = ""
    if ($proc) {
        try {
            $startTime = $proc.StartTime.ToUniversalTime().ToString("o")
        } catch {
            $startTime = ""
        }
    }
    $pidInfo = $null
    if ($row.pid_json -and (Test-Path -LiteralPath ([string]$row.pid_json))) {
        try {
            $pidInfo = Get-Content -LiteralPath ([string]$row.pid_json) -Raw | ConvertFrom-Json
        } catch {
            $pidInfo = $null
        }
    }
    $rows += [pscustomobject]@{
        name = $row.name
        script = $row.script
        process_id = $targetPid
        manifest_process_id = $manifestPid
        process_id_source = $pidSource
        launcher_pid = $row.launcher_pid
        task_name = $row.task_name
        process_name = $processName
        running = [bool]$expectedProcess
        running_status = if ($expectedProcess) { "RUNNING_EXPECTED_PYTHON" } elseif ($proc) { "PID_REUSED_OR_UNEXPECTED_PROCESS" } else { "MISSING_PROCESS" }
        start_time = $startTime
        cpu_seconds = if ($proc -and $proc.CPU -ne $null) { [math]::Round($proc.CPU, 3) } else { "" }
        working_set_mb = if ($proc) { [math]::Round($proc.WorkingSet64 / 1MB, 1) } else { "" }
        private_mb = if ($proc) { [math]::Round($proc.PrivateMemorySize64 / 1MB, 1) } else { "" }
        child_exit_code = if ($pidInfo -and ($pidInfo.PSObject.Properties.Name -contains "exit_code")) { $pidInfo.exit_code } else { "" }
        child_exited_at_utc = if ($pidInfo -and $pidInfo.exited_at_utc) { $pidInfo.exited_at_utc } else { "" }
    }
}

[pscustomobject]@{
    repo_root = $RepoRoot
    checked_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    status = if (($rows | Where-Object { $_.running }).Count -eq @($manifest.started).Count) { "ALL_RUNNING" } else { "MISSING_PROCESS" }
    rows = $rows
} | ConvertTo-Json -Depth 5
