param(
    [switch]$Execute,
    [switch]$IUnderstandThisRestartsPaperShadows,
    [switch]$IUnderstandUnmanagedBtcProcessesRemain,
    [switch]$KeepExistingTradeDbs,
    [int]$StartupWaitSec = 8,
    [string]$SinceUtc = "2026-05-18T02:42:00+00:00"
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $repo "backtest_outputs\btc_paper_shadow_controlled_restart_$timestamp"
$logDir = Join-Path $outDir "process_logs"
$backupDir = Join-Path $outDir "archived_trade_dbs"
New-Item -ItemType Directory -Force -Path $outDir, $logDir, $backupDir | Out-Null

$homeDir = [Environment]::GetFolderPath("UserProfile")
$python = (Get-Command python).Source

$targets = @(
    [pscustomobject][ordered]@{
        Name = "btc15m_q250_qty500_firstskip_shadow"
        Script = "scripts\btc15m_f2_q250_qty500_firstskip_shadow.py"
        TradeDb = Join-Path $repo ".codex_work\btc15m_f2_q250_qty500_firstskip_shadow\btc15m_f2_q250_qty500_firstskip_shadow_trades.db"
        Args = @("-u", "scripts\btc15m_f2_q250_qty500_firstskip_shadow.py")
    },
    [pscustomobject][ordered]@{
        Name = "btc15m_q250_qty500_firstskip_yes_shadow"
        Script = "scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py"
        TradeDb = Join-Path $repo ".codex_work\btc15m_f2_q250_qty500_firstskip_yes_shadow\btc15m_f2_q250_qty500_firstskip_yes_shadow_trades.db"
        Args = @("-u", "scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py")
    },
    [pscustomobject][ordered]@{
        Name = "btc15m_q1000_yes_shadow"
        Script = "scripts\btc15m_f2_q1000_yes_shadow.py"
        TradeDb = Join-Path $repo ".codex_work\btc15m_f2_q1000_yes_shadow\btc15m_f2_q1000_yes_shadow_trades.db"
        Args = @("-u", "scripts\btc15m_f2_q1000_yes_shadow.py")
    },
    [pscustomobject][ordered]@{
        Name = "btc1h_high_conf80_entry70_no_chase_shadow"
        Script = "scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py"
        TradeDb = Join-Path $homeDir ".btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow.db"
        Args = @("-u", "scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py")
    }
)

$untouchedProcesses = @("scripts\btc15m_live_capture.py")
$script:ProcessInspectionWarnings = [System.Collections.Generic.List[string]]::new()

function Normalize-CommandLine {
    param([string]$Text)
    if ($null -eq $Text) {
        return ""
    }
    return $Text.Replace("/", "\").ToLowerInvariant()
}

function Test-CommandMatchesScript {
    param(
        [string]$CommandLine,
        [string]$Script
    )
    $cmd = Normalize-CommandLine -Text $CommandLine
    $needle = (Normalize-CommandLine -Text $Script)
    return ($cmd -like "*$needle*")
}

function Get-CaptureStatusPathForScript {
    param([string]$Script)
    switch ($Script) {
        "scripts\btc15m_f2_q250_qty500_firstskip_shadow.py" {
            return (Join-Path $repo ".codex_work\btc15m_f2_q250_qty500_firstskip_shadow\btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb.status.json")
        }
        "scripts\btc15m_f2_q250_qty500_firstskip_yes_shadow.py" {
            return (Join-Path $repo ".codex_work\btc15m_f2_q250_qty500_firstskip_yes_shadow\btc15m_f2_q250_qty500_firstskip_yes_shadow_capture.duckdb.status.json")
        }
        "scripts\btc15m_f2_q1000_yes_shadow.py" {
            return (Join-Path $repo ".codex_work\btc15m_f2_q1000_yes_shadow\btc15m_f2_q1000_yes_shadow_capture.duckdb.status.json")
        }
        "scripts\btc_1hr_high_conf80_entry70_no_chase_shadow.py" {
            return (Join-Path $homeDir ".btc_kalshi_bot\btc_1hr_high_conf80_entry70_no_chase_shadow_capture.duckdb.status.json")
        }
        "scripts\btc15m_live_capture.py" {
            return (Join-Path $homeDir ".btc_kalshi_bot\btc15m_live_capture.duckdb.status.json")
        }
        default {
            return ""
        }
    }
}

function Get-TargetProcessFromStatusSidecar {
    param([string]$Script)
    $statusPath = Get-CaptureStatusPathForScript -Script $Script
    if (-not $statusPath -or -not (Test-Path -LiteralPath $statusPath)) {
        return @()
    }
    try {
        $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        $pid = [int]$status.pid
    } catch {
        return @()
    }
    if ($pid -le 0) {
        return @()
    }
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if (-not $proc -or ([string]$proc.ProcessName) -notlike "python*") {
        return @()
    }
    @([pscustomobject][ordered]@{
        ProcessId = $pid
        CommandLine = "$Script via capture_status_sidecar"
        InspectionSource = "capture_status_sidecar_pid"
    })
}

function Get-TargetProcesses {
    param([string]$Script)
    try {
        $all = Get-CimInstance Win32_Process -Filter "name = 'python.exe'"
        return @($all | Where-Object { Test-CommandMatchesScript -CommandLine $_.CommandLine -Script $Script })
    } catch {
        $script:ProcessInspectionWarnings.Add("Get-CimInstance denied for target process inspection; using capture status sidecar PID fallback for $Script")
        return @(Get-TargetProcessFromStatusSidecar -Script $Script)
    }
}

function Get-MatchingBtcPythonProcesses {
    try {
        $all = Get-CimInstance Win32_Process -Filter "name = 'python.exe'"
        return @($all | Where-Object {
            $_.CommandLine -like "*Kalshi-Trading-Bot*" -or
            $_.CommandLine -like "*btc15m*" -or
            $_.CommandLine -like "*btc_1hr*" -or
            $_.CommandLine -like "*predexon*"
        })
    } catch {
        $script:ProcessInspectionWarnings.Add("Get-CimInstance denied for unmanaged process inspection; unmanaged command-line process list unavailable")
        return @()
    }
}

function Get-UnmanagedMatchingProcesses {
    param([object[]]$KnownScripts)
    $all = Get-MatchingBtcPythonProcesses
    @($all | Where-Object {
        $cmd = $_.CommandLine
        $known = $false
        foreach ($script in $KnownScripts) {
            if (Test-CommandMatchesScript -CommandLine $cmd -Script $script) {
                $known = $true
                break
            }
        }
        -not $known
    })
}

function Invoke-RepoPython {
    param([string[]]$Args)
    & $python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "python $($Args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Test-TargetScriptSafety {
    param([string]$Script)
    $fullPath = Join-Path $repo $Script
    $reasons = [System.Collections.Generic.List[string]]::new()

    if (-not (Test-Path -LiteralPath $fullPath)) {
        $reasons.Add("target_script_missing")
        return [pscustomobject][ordered]@{
            pass = $false
            reasons = ($reasons -join ";")
        }
    }

    if ($Script -notlike "*_shadow.py") {
        $reasons.Add("target_script_not_shadow_wrapper")
    }
    if ($Script -like "*_live.py") {
        $reasons.Add("target_script_looks_like_live_runner")
    }

    $text = Get-Content -Raw -LiteralPath $fullPath
    $btc15mPaperMode = $text -match '"--mode"\s*,\s*"paper"'
    $btc1hPaperFlag = $text -match '"--paper"'
    if (-not ($btc15mPaperMode -or $btc1hPaperFlag)) {
        $reasons.Add("paper_mode_not_locked_in_wrapper")
    }

    if ($text -match '"--mode"\s*,\s*"live"') {
        $reasons.Add("wrapper_contains_live_mode_argv")
    }

    return [pscustomobject][ordered]@{
        pass = ($reasons.Count -eq 0)
        reasons = ($reasons -join ";")
    }
}

$planRows = foreach ($target in $targets) {
    $procs = Get-TargetProcesses -Script $target.Script
    $safety = Test-TargetScriptSafety -Script $target.Script
    $processCount = @($procs).Count
    $duplicateCount = [Math]::Max(0, $processCount - 1)
    $processHygieneStatus = if ($processCount -eq 0) {
        "NOT_RUNNING"
    } elseif ($duplicateCount -gt 0) {
        "DUPLICATE_TARGET_PROCESSES"
    } else {
        "ONE_TARGET_PROCESS"
    }
    [pscustomobject][ordered]@{
        name = $target.Name
        script = $target.Script
        script_safety_pass = [bool]$safety.pass
        script_safety_reasons = [string]$safety.reasons
        trade_db = $target.TradeDb
        trade_db_exists = Test-Path -LiteralPath $target.TradeDb
        matching_pids = @($procs | ForEach-Object { [int]$_.ProcessId })
        process_count = $processCount
        duplicate_process_count = $duplicateCount
        process_hygiene_status = $processHygieneStatus
        will_archive_trade_db = -not $KeepExistingTradeDbs
        stdout_log = Join-Path $logDir "$($target.Name).out.log"
        stderr_log = Join-Path $logDir "$($target.Name).err.log"
    }
}

$knownScripts = @($targets | ForEach-Object { $_.Script }) + $untouchedProcesses
$unmanagedProcesses = @(Get-UnmanagedMatchingProcesses -KnownScripts $knownScripts)
$unmanagedRows = @($unmanagedProcesses | ForEach-Object {
    [pscustomobject][ordered]@{
        process_id = [int]$_.ProcessId
        command_line = [string]$_.CommandLine
    }
})

$plan = [ordered]@{
    created_at = (Get-Date).ToString("o")
    repo = $repo
    execute = [bool]$Execute
    keep_existing_trade_dbs = [bool]$KeepExistingTradeDbs
    untouched_processes = $untouchedProcesses
    unmanaged_matching_process_count = $unmanagedRows.Count
    unmanaged_matching_processes = $unmanagedRows
    unmanaged_process_acknowledgement = [bool]$IUnderstandUnmanagedBtcProcessesRemain
    process_inspection_warnings = @($script:ProcessInspectionWarnings)
    targets = $planRows
    warning = "This workflow starts/restarts paper shadows only. It does not deploy live trading and intentionally leaves capture-only BTC15M running."
}
$planPath = Join-Path $outDir "restart_plan.json"
$plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $planPath -Encoding UTF8

Write-Host "BTC paper shadow controlled restart plan:"
$planRows | Format-Table name, script, script_safety_pass, script_safety_reasons, trade_db_exists, matching_pids, process_count, duplicate_process_count, process_hygiene_status, will_archive_trade_db -AutoSize
if ($unmanagedRows.Count -ne 0) {
    Write-Host ""
    Write-Host "Unmanaged matching BTC/Predexon Python processes were found. They are not touched by this restart workflow:"
    $unmanagedRows | Format-Table process_id, command_line -AutoSize
}
if ($script:ProcessInspectionWarnings.Count -ne 0) {
    Write-Host ""
    Write-Host "Process inspection warnings:"
    $script:ProcessInspectionWarnings | ForEach-Object { Write-Host "  $_" }
}
Write-Host "Plan written to $planPath"

if (-not $Execute) {
    Write-Host ""
    Write-Host "Dry run only. No processes were stopped or started."
    Write-Host "To execute later, run:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\restart_btc_paper_shadows.ps1 -Execute -IUnderstandThisRestartsPaperShadows"
    if ($unmanagedRows.Count -ne 0) {
        Write-Host "  Note: execution will refuse while unmanaged matching processes remain unless that risk is explicitly acknowledged."
    }
    exit 0
}

if (-not $IUnderstandThisRestartsPaperShadows) {
    throw "Refusing to execute without -IUnderstandThisRestartsPaperShadows."
}

$unsafeTargets = @($planRows | Where-Object { -not $_.script_safety_pass })
if ($unsafeTargets.Count -ne 0) {
    $messages = $unsafeTargets | ForEach-Object { "$($_.name): $($_.script_safety_reasons)" }
    throw "Refusing to execute because target script safety checks failed: $($messages -join ' | ')"
}

if ($unmanagedRows.Count -ne 0 -and (-not $IUnderstandUnmanagedBtcProcessesRemain)) {
    $messages = $unmanagedRows | ForEach-Object { "PID $($_.process_id): $($_.command_line)" }
    throw "Refusing to execute because unmanaged matching BTC/Predexon Python processes exist and would remain running: $($messages -join ' | ')"
}

Write-Host ""
Write-Host "Running non-destructive restart-path preflight before touching processes..."
Invoke-RepoPython @("scripts\build_btc_shadow_restart_preflight.py", "--out-dir", (Join-Path $outDir "pre_restart_path_preflight"))

foreach ($target in $targets) {
    $procs = Get-TargetProcesses -Script $target.Script
    foreach ($proc in $procs) {
        Write-Host "Stopping $($target.Name) PID $($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force
    }
}

$deadline = (Get-Date).AddSeconds(25)
do {
    $remaining = @()
    foreach ($target in $targets) {
        $remaining += Get-TargetProcesses -Script $target.Script
    }
    if ($remaining.Count -eq 0) {
        break
    }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

if ($remaining.Count -ne 0) {
    $pids = ($remaining | ForEach-Object { $_.ProcessId }) -join ","
    throw "Some target processes are still running after stop request: $pids"
}

foreach ($target in $targets) {
    if ((Test-Path -LiteralPath $target.TradeDb) -and (-not $KeepExistingTradeDbs)) {
        $dest = Join-Path $backupDir "$($target.Name)_trades_$timestamp.db"
        Write-Host "Archiving stale trade DB $($target.TradeDb) -> $dest"
        Move-Item -LiteralPath $target.TradeDb -Destination $dest
    }
}

$started = @()
foreach ($target in $targets) {
    $stdout = Join-Path $logDir "$($target.Name).out.log"
    $stderr = Join-Path $logDir "$($target.Name).err.log"
    Write-Host "Starting $($target.Name): python $($target.Args -join ' ')"
    $proc = Start-Process `
        -FilePath $python `
        -ArgumentList $target.Args `
        -WorkingDirectory $repo `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    $started += [ordered]@{
        name = $target.Name
        process_id = $proc.Id
        stdout_log = $stdout
        stderr_log = $stderr
    }
}

Start-Sleep -Seconds $StartupWaitSec

Invoke-RepoPython @("scripts\build_btc_ledger_schema_preflight.py", "--out-dir", (Join-Path $outDir "post_restart_schema_preflight"))
Invoke-RepoPython @("scripts\check_btc_forward_shadow_status.py", "--out-dir", (Join-Path $outDir "post_restart_shadow_status"), "--since-utc", $SinceUtc)

$post = [ordered]@{
    completed_at = (Get-Date).ToString("o")
    started = $started
    out_dir = $outDir
    next_required_checks = @(
        "Inspect post_restart_schema_preflight\ledger_schema_preflight_summary.csv",
        "Inspect post_restart_shadow_status\shadow_status.csv",
        "Only future paper_filled rows written after this controlled start/restart can count as deployable ledger evidence after official settlement."
    )
}
$post | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $outDir "restart_result.json") -Encoding UTF8

Write-Host ""
Write-Host "Controlled paper shadow start/restart completed."
Write-Host "Result directory: $outDir"
Write-Host "Review post-restart schema/status artifacts before counting any future fills."
