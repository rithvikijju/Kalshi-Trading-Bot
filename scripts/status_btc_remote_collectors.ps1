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
foreach ($row in @($manifest.started)) {
    $targetPid = [int]$row.process_id
    $proc = if ($targetPid -gt 0) { Get-Process -Id $targetPid -ErrorAction SilentlyContinue } else { $null }
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
        launcher_pid = $row.launcher_pid
        task_name = $row.task_name
        running = [bool]$proc
        start_time = if ($proc) { $proc.StartTime.ToUniversalTime().ToString("o") } else { "" }
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
