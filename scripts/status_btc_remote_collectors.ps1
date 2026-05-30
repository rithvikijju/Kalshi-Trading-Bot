param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$runtimeDir = Join-Path $RepoRoot "runtime"
$manifestPath = Join-Path $runtimeDir "btc_collectors_processes.json"

if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
} else {
    $manifest = [pscustomobject]@{ started = @() }
}
$rows = @()
$currentActiveTargets = @(
    [pscustomobject]@{
        name = "btc15m_live_capture"
        script = "scripts\btc15m_live_capture.py"
        process_id = 0
        launcher_pid = 0
        task_name = "KalshiBTC_btc15m_live_capture"
        pid_json = ""
    },
    [pscustomobject]@{
        name = "btc15m_lowdd_forward_shadow"
        script = "scripts\btc15m_lowdd_live.py"
        process_id = 0
        launcher_pid = 0
        task_name = "KalshiBTC_btc15m_lowdd_forward_shadow"
        pid_json = ""
    }
)
$currentActiveNames = @($currentActiveTargets | ForEach-Object { [string]$_.name })

function Get-CaptureStatusPath {
    param(
        [string]$RepoRoot,
        [string]$Name
    )
    switch ($Name) {
        "btc15m_live_capture" { return (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc15m_live_capture.duckdb.status.json") }
        "btc15m_lowdd_forward_shadow" { return (Join-Path $env:USERPROFILE ".btc_kalshi_bot\btc15m_lowdd_forward_shadow_capture.duckdb.status.json") }
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

function Get-TaskRuntimeStatus {
    param([string]$TaskName)
    if (!$TaskName) {
        return [pscustomobject]@{
            task_exists = $false
            task_runtime_status = ""
            task_status_source = "no_task_name"
            task_status_error = ""
        }
    }
    try {
        if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($task) {
                return [pscustomobject]@{
                    task_exists = $true
                    task_runtime_status = [string]$task.State
                    task_status_source = "Get-ScheduledTask"
                    task_status_error = ""
                }
            }
        }
    } catch {
        # Fall back to schtasks below.
    }
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = cmd.exe /c "schtasks /Query /TN `"$TaskName`" /FO LIST /V" 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($exitCode -ne 0) {
        return [pscustomobject]@{
            task_exists = $false
            task_runtime_status = "MISSING_TASK"
            task_status_source = "schtasks"
            task_status_error = ($output -join "`n")
        }
    }
    $status = ""
    foreach ($line in @($output)) {
        if ([string]$line -match '^\s*Status:\s*(.+?)\s*$') {
            $status = $Matches[1].Trim()
            break
        }
    }
    return [pscustomobject]@{
        task_exists = $true
        task_runtime_status = $status
        task_status_source = "schtasks"
        task_status_error = ""
    }
}

function Get-TaskSupervisionStatus {
    param(
        [bool]$ExpectedProcess,
        [string]$TaskName,
        [object]$TaskStatus
    )
    if (!$TaskName) {
        return "NO_TASK_DIRECT_LAUNCH"
    }
    if (!$TaskStatus.task_exists) {
        return "TASK_MISSING"
    }
    $runtime = ([string]$TaskStatus.task_runtime_status).Trim().ToLowerInvariant()
    if ($ExpectedProcess -and $runtime -ne "running") {
        return "PROCESS_RUNNING_TASK_NOT_RUNNING"
    }
    if (!$ExpectedProcess -and $runtime -eq "running") {
        return "TASK_RUNNING_PROCESS_MISSING"
    }
    if ($ExpectedProcess) {
        return "TASK_PROCESS_ALIGNED"
    }
    return "PROCESS_AND_TASK_NOT_RUNNING"
}

function New-CollectorStatusRow {
    param(
        [object]$Row,
        [string]$TargetSet
    )
    $manifestTaskName = [string]$row.task_name
    $expectedTaskName = $manifestTaskName
    if (!$expectedTaskName -and $row.name) {
        $expectedTaskName = "KalshiBTC_$([string]$row.name)"
    }
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
    $taskStatus = Get-TaskRuntimeStatus -TaskName $expectedTaskName
    $supervisionStatus = Get-TaskSupervisionStatus -ExpectedProcess ([bool]$expectedProcess) -TaskName $expectedTaskName -TaskStatus $taskStatus
    return [pscustomobject]@{
        name = $row.name
        script = $row.script
        target_set = $TargetSet
        current_active = ($currentActiveNames -contains ([string]$row.name))
        process_id = $targetPid
        manifest_process_id = $manifestPid
        process_id_source = $pidSource
        launcher_pid = $row.launcher_pid
        manifest_task_name = $manifestTaskName
        task_name = $expectedTaskName
        process_name = $processName
        running = [bool]$expectedProcess
        running_status = if ($expectedProcess) { "RUNNING_EXPECTED_PYTHON" } elseif ($proc) { "PID_REUSED_OR_UNEXPECTED_PROCESS" } else { "MISSING_PROCESS" }
        task_exists = $taskStatus.task_exists
        task_runtime_status = $taskStatus.task_runtime_status
        task_status_source = $taskStatus.task_status_source
        task_status_error = $taskStatus.task_status_error
        task_supervision_status = $supervisionStatus
        start_time = $startTime
        cpu_seconds = if ($proc -and $proc.CPU -ne $null) { [math]::Round($proc.CPU, 3) } else { "" }
        working_set_mb = if ($proc) { [math]::Round($proc.WorkingSet64 / 1MB, 1) } else { "" }
        private_mb = if ($proc) { [math]::Round($proc.PrivateMemorySize64 / 1MB, 1) } else { "" }
        child_exit_code = if ($pidInfo -and ($pidInfo.PSObject.Properties.Name -contains "exit_code")) { $pidInfo.exit_code } else { "" }
        child_exited_at_utc = if ($pidInfo -and $pidInfo.exited_at_utc) { $pidInfo.exited_at_utc } else { "" }
    }
}

$manifestNames = @{}
foreach ($row in @($manifest.started)) {
    $manifestNames[[string]$row.name] = $true
    $targetSet = if ($currentActiveNames -contains ([string]$row.name)) { "current_active_manifest" } else { "manifest_legacy" }
    $rows += New-CollectorStatusRow -Row $row -TargetSet $targetSet
}
foreach ($target in $currentActiveTargets) {
    if (!$manifestNames.ContainsKey([string]$target.name)) {
        $rows += New-CollectorStatusRow -Row $target -TargetSet "current_active_observed"
    }
}

$manifestRows = @($rows | Where-Object { $_.target_set -in @("current_active_manifest", "manifest_legacy") })
$currentRows = @($rows | Where-Object { $_.current_active })

[pscustomobject]@{
    repo_root = $RepoRoot
    manifest = $manifestPath
    checked_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    status = if (!(Test-Path -LiteralPath $manifestPath)) {
        "NO_MANIFEST"
    } elseif (@($manifestRows | Where-Object { $_.running }).Count -eq @($manifestRows).Count) {
        "ALL_RUNNING"
    } else {
        "MISSING_PROCESS"
    }
    current_active_status = if (@($currentRows | Where-Object { $_.running }).Count -eq @($currentRows).Count) {
        "ALL_CURRENT_ACTIVE_RUNNING"
    } else {
        "CURRENT_ACTIVE_MISSING_PROCESS"
    }
    supervision_status = if (@($rows | Where-Object { $_.task_supervision_status -eq "PROCESS_RUNNING_TASK_NOT_RUNNING" }).Count -gt 0) {
        "PROCESS_RUNNING_TASK_NOT_RUNNING"
    } elseif (@($rows | Where-Object { $_.task_supervision_status -eq "TASK_MISSING" }).Count -gt 0) {
        "TASK_MISSING"
    } elseif (@($rows | Where-Object { $_.task_supervision_status -eq "TASK_RUNNING_PROCESS_MISSING" }).Count -gt 0) {
        "TASK_RUNNING_PROCESS_MISSING"
    } else {
        "TASK_SUPERVISION_OK"
    }
    rows = $rows
} | ConvertTo-Json -Depth 5
