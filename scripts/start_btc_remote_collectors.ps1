param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Python = "",
    [ValidateSet("direct", "task")]
    [string]$LaunchMode = "direct",
    [switch]$SkipStopExisting
)

$ErrorActionPreference = "Stop"

function Resolve-Python {
    param([string]$RepoRoot, [string]$Python)
    if ($Python) {
        return $Python
    }
    $basePython = "C:\Python310\python.exe"
    if (Test-Path -LiteralPath $basePython) {
        return $basePython
    }
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    $cmd = Get-Command python -ErrorAction Stop
    return $cmd.Source
}

function Read-CredentialPath {
    param([string]$RepoRoot)
    $creds = Join-Path $RepoRoot "credentials.env"
    if (!(Test-Path -LiteralPath $creds)) {
        throw "Missing credentials.env in $RepoRoot. Deploy credentials explicitly before starting collectors."
    }
    $privateKey = ""
    foreach ($line in Get-Content -LiteralPath $creds) {
        if ($line -match '^\s*PRIVATE_KEY_PATH\s*=\s*(.+?)\s*$') {
            $privateKey = $Matches[1]
            break
        }
    }
    if (!$privateKey -or !(Test-Path -LiteralPath $privateKey)) {
        throw "credentials.env exists, but PRIVATE_KEY_PATH does not point to a readable file on this machine."
    }
}

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

function Read-FreshCaptureStatusPid {
    param(
        [string]$Path,
        [datetime]$MinUpdatedUtc
    )
    if (!$Path -or !(Test-Path -LiteralPath $Path)) {
        return 0
    }
    try {
        $obj = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $updated = [datetimeoffset]::Parse([string]$obj.updated_at_utc).UtcDateTime
        if ($updated -lt $MinUpdatedUtc) {
            return 0
        }
        return [int]$obj.pid
    } catch {
        return 0
    }
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$Python = Resolve-Python -RepoRoot $RepoRoot -Python $Python
Read-CredentialPath -RepoRoot $RepoRoot

$runtimeDir = Join-Path $RepoRoot "runtime"
$logDir = Join-Path $runtimeDir "logs"
$taskDir = Join-Path $runtimeDir "tasks"
$pidDir = Join-Path $runtimeDir "pids"
New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir, $taskDir, $pidDir | Out-Null

if (!$SkipStopExisting) {
    & (Join-Path $PSScriptRoot "stop_btc_remote_collectors.ps1") -RepoRoot $RepoRoot -Quiet
}

$targets = @(
    @{ name = "btc15m_live_capture"; script = "scripts\btc15m_live_capture.py" },
    @{ name = "btc15m_lowdd_forward_shadow"; script = "scripts\btc15m_lowdd_live.py" }
)

$env:BTC15M_CAPTURE_WRITER = "persistent"
$venvRoot = Join-Path $RepoRoot ".venv"
$venvSite = Join-Path $venvRoot "Lib\site-packages"
if (Test-Path -LiteralPath $venvSite) {
    $env:VIRTUAL_ENV = $venvRoot
    $env:PATH = (Join-Path $venvRoot "Scripts") + ";" + $env:PATH
    if ($env:PYTHONPATH) {
        $env:PYTHONPATH = $RepoRoot + ";" + $venvSite + ";" + $env:PYTHONPATH
    } else {
        $env:PYTHONPATH = $RepoRoot + ";" + $venvSite
    }
}

$started = @()
$now = Get-Date -Format "yyyyMMdd_HHmmss"
$taskStartTime = (Get-Date).AddMinutes(5).ToString("HH:mm")
foreach ($target in $targets) {
    $targetStartUtc = (Get-Date).ToUniversalTime()
    $stdout = Join-Path $logDir "$($target.name)_$now.out.log"
    $stderr = Join-Path $logDir "$($target.name)_$now.err.log"
    $pidJson = Join-Path $pidDir "$($target.name)_$now.pid.json"
    $latestPidJson = Join-Path $pidDir "$($target.name).pid.json"
    $wrapper = Join-Path $taskDir "$($target.name)_launcher.ps1"
    $taskName = "KalshiBTC_$($target.name)"
    Remove-Item -LiteralPath $latestPidJson -Force -ErrorAction SilentlyContinue
    if ($LaunchMode -eq "direct") {
        $proc = Start-Process -FilePath $Python -ArgumentList @("-u", $target.script) -WorkingDirectory $RepoRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
        $startRecord = [pscustomobject]@{
            name = $target.name
            script = $target.script
            launcher_pid = 0
            child_process_id = $proc.Id
            started_at_utc = (Get-Date).ToUniversalTime().ToString("o")
            stdout = $stdout
            stderr = $stderr
        }
        $startRecord | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $pidJson -Encoding UTF8
        $startRecord | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $latestPidJson -Encoding UTF8
        $started += [pscustomobject]@{
            name = $target.name
            script = $target.script
            process_id = [int]$proc.Id
            launcher_pid = 0
            task_name = ""
            wrapper = ""
            pid_json = $pidJson
            started_at_utc = $startRecord.started_at_utc
            stdout = $stdout
            stderr = $stderr
        }
        continue
    }
    $wrapperText = @"
`$ErrorActionPreference = "Stop"
`$repoRoot = "$RepoRoot"
`$python = "$Python"
`$script = "$($target.script)"
`$stdout = "$stdout"
`$stderr = "$stderr"
`$pidJson = "$pidJson"
`$latestPidJson = "$latestPidJson"
Set-Location -LiteralPath `$repoRoot
`$env:BTC15M_CAPTURE_WRITER = "persistent"
`$venvRoot = Join-Path `$repoRoot ".venv"
`$venvSite = Join-Path `$venvRoot "Lib\site-packages"
if (Test-Path -LiteralPath `$venvSite) {
    `$env:VIRTUAL_ENV = `$venvRoot
    `$env:PATH = (Join-Path `$venvRoot "Scripts") + ";" + `$env:PATH
    if (`$env:PYTHONPATH) {
        `$env:PYTHONPATH = `$repoRoot + ";" + `$venvSite + ";" + `$env:PYTHONPATH
    } else {
        `$env:PYTHONPATH = `$repoRoot + ";" + `$venvSite
    }
}
`$proc = Start-Process -FilePath `$python -ArgumentList @("-u", `$script) -WorkingDirectory `$repoRoot -RedirectStandardOutput `$stdout -RedirectStandardError `$stderr -WindowStyle Hidden -PassThru
`$startRecord = [pscustomobject]@{
    name = "$($target.name)"
    script = `$script
    launcher_pid = `$PID
    child_process_id = `$proc.Id
    started_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    stdout = `$stdout
    stderr = `$stderr
}
`$startRecord | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath `$pidJson -Encoding UTF8
`$startRecord | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath `$latestPidJson -Encoding UTF8
`$proc.WaitForExit()
`$exitRecord = [pscustomobject]@{
    name = "$($target.name)"
    script = `$script
    launcher_pid = `$PID
    child_process_id = `$proc.Id
    started_at_utc = (Get-Content -LiteralPath `$pidJson -Raw | ConvertFrom-Json).started_at_utc
    exited_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    exit_code = `$proc.ExitCode
    stdout = `$stdout
    stderr = `$stderr
}
`$exitRecord | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath `$pidJson -Encoding UTF8
`$exitRecord | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath `$latestPidJson -Encoding UTF8
"@
    $wrapperText | Set-Content -LiteralPath $wrapper -Encoding UTF8

    cmd.exe /c "schtasks /Delete /TN `"$taskName`" /F >nul 2>nul" | Out-Null
    $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
    $createOutput = cmd.exe /c "schtasks /Create /TN `"$taskName`" /SC ONCE /ST $taskStartTime /TR `"$taskCommand`" /F" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create scheduled task $taskName`: $createOutput"
    }
    try {
        if (Get-Command New-ScheduledTaskSettingsSet -ErrorAction SilentlyContinue) {
            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
            Set-ScheduledTask -TaskName $taskName -Settings $settings | Out-Null
        }
    } catch {
        Write-Warning "Failed to relax scheduled-task runtime/power limits for $taskName`: $($_.Exception.Message)"
    }
    $runOutput = cmd.exe /c "schtasks /Run /TN `"$taskName`"" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start scheduled task $taskName`: $runOutput"
    }

    $pidInfo = $null
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $pidJson) {
            try {
                $pidInfo = Get-Content -LiteralPath $pidJson -Raw | ConvertFrom-Json
                if ($pidInfo.child_process_id -and $pidInfo.stdout -eq $stdout) {
                    break
                }
            } catch {
                $pidInfo = $null
            }
        }
        Start-Sleep -Milliseconds 250
    }
    $childPid = if ($pidInfo -and $pidInfo.child_process_id) { [int]$pidInfo.child_process_id } else { 0 }
    $statusPath = Get-CaptureStatusPath -RepoRoot $RepoRoot -Name $target.name
    $statusPid = Read-FreshCaptureStatusPid -Path $statusPath -MinUpdatedUtc $targetStartUtc
    if ($statusPid -gt 0) {
        $childPid = $statusPid
    }
    if ($childPid -le 0) {
        $statusDeadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $statusDeadline -and $childPid -le 0) {
            $childPid = Read-FreshCaptureStatusPid -Path $statusPath -MinUpdatedUtc $targetStartUtc
            if ($childPid -le 0) {
                Start-Sleep -Milliseconds 500
            }
        }
    }
    $launcherPid = if ($pidInfo -and $pidInfo.launcher_pid) { [int]$pidInfo.launcher_pid } else { 0 }
    $started += [pscustomobject]@{
        name = $target.name
        script = $target.script
        process_id = $childPid
        launcher_pid = $launcherPid
        task_name = $taskName
        wrapper = $wrapper
        pid_json = $pidJson
        started_at_utc = if ($pidInfo -and $pidInfo.started_at_utc) { $pidInfo.started_at_utc } else { (Get-Date).ToUniversalTime().ToString("o") }
        stdout = $stdout
        stderr = $stderr
    }
}

$manifest = [pscustomobject]@{
    repo_root = $RepoRoot
    python = $Python
    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    started = $started
    stop_policy = "manifest_pids_only_plus_exact_btc_script_sweep_when_commandline_access_is_available"
}
$manifestPath = Join-Path $runtimeDir "btc_collectors_processes.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$manifest | ConvertTo-Json -Depth 5
