param(
    [switch]$Copy,
    [switch]$CopyBrowserSafe,
    [switch]$OpenChatGPT
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$argsList = @("scripts\build_gpt_pro_strategy_packet.py")
if ($Copy) {
    $argsList += "--copy"
}
if ($CopyBrowserSafe) {
    $argsList += "--copy-browser-safe"
}
if ($OpenChatGPT) {
    $argsList += "--open-chatgpt"
}

python @argsList

$latest = Join-Path $repo "gpt_pro_packets\latest_strategy_advisor_packet.json"
if (Test-Path $latest) {
    $manifest = Get-Content -Raw -LiteralPath $latest | ConvertFrom-Json
    Write-Host ""
    Write-Host "GPT Pro packet ready:"
    Write-Host "  Prompt:   $($manifest.prompt)"
    if ($manifest.browser_safe_prompt) {
        Write-Host "  Browser-safe prompt: $($manifest.browser_safe_prompt)"
    }
    Write-Host "  Evidence: $($manifest.evidence_bundle)"
    if ($manifest.evidence_bundle_compact) {
        Write-Host "  Compact evidence: $($manifest.evidence_bundle_compact)"
    }
    Write-Host "  Zip:      $($manifest.zip)"
    Write-Host ""
    if ($CopyBrowserSafe) {
        Write-Host "Use ChatGPT Pro's strongest reasoning model. Paste the browser-safe prompt directly."
    } else {
        Write-Host "Use ChatGPT Pro's strongest reasoning model. Paste the prompt and upload the zip."
    }
}
