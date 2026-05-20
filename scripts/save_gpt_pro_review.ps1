param(
    [string]$Name = "strategy_review"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$reviewDir = Join-Path $repo "docs\gpt_pro_reviews"
New-Item -ItemType Directory -Force -Path $reviewDir | Out-Null

$safeName = ($Name -replace "[^A-Za-z0-9_-]", "_").Trim("_")
if ([string]::IsNullOrWhiteSpace($safeName)) {
    $safeName = "strategy_review"
}

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$outPath = Join-Path $reviewDir "gpt_pro_${safeName}_$ts.md"
$clipboard = Get-Clipboard -Raw
if ([string]::IsNullOrWhiteSpace($clipboard)) {
    throw "Clipboard is empty. Copy the GPT Pro answer first."
}

$latest = Join-Path $repo "gpt_pro_packets\latest_strategy_advisor_packet.json"
$header = @(
    "# GPT Pro Review: $safeName"
    ""
    "Saved: $(Get-Date -Format o)"
)
if (Test-Path $latest) {
    $header += "Packet manifest: ``$latest``"
}
$header += ""
$header += "---"
$header += ""

$body = ($header -join "`n") + $clipboard
Set-Content -LiteralPath $outPath -Value $body -Encoding UTF8
Write-Host "Saved GPT Pro review: $outPath"
