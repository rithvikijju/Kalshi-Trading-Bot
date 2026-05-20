#!/usr/bin/env python3
"""Build a sanitized GPT Pro strategy-advisor packet.

This packages the current BTC/Kalshi research state into a small set of files
that can be pasted/uploaded into ChatGPT Pro. It deliberately excludes raw
DuckDB/Parquet/log data and credentials; the goal is to ask for a better plan
from a high-context model without leaking secrets or overwhelming it with
multi-GB artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_ROOT = PROJECT_ROOT / "backtest_outputs"
DEFAULT_OUT_ROOT = PROJECT_ROOT / "gpt_pro_packets"

MAX_REPORT_CHARS = 18_000
MAX_LEDGER_CHARS = 45_000
MAX_CSV_ROWS = 40
COMPACT_REPORT_CHARS = 2_500
COMPACT_LEDGER_CHARS = 8_000
COMPACT_CSV_ROWS = 5


IMPORTANT_PREFIXES = {
    "deployment_readiness_": "Deployment readiness gate",
    "btc_gpt_pro_action_status_": "BTC GPT Pro action/status checklist",
    "btc_forward_evidence_report_": "BTC forward evidence report",
    "btc_evidence_stack_refresh_": "BTC sequential evidence-stack refresh",
    "btc_strategy_triage_": "BTC strategy triage and next-action ranking",
    "btc_kill_continue_": "BTC kill-or-continue control report",
    "btc_forward_consistency_audit_": "BTC forward consistency audit",
    "btc_forward_row_reconciliation_": "BTC paper-vs-live-replay row reconciliation",
    "btc1h_replay_coverage_audit_": "BTC1H replay coverage audit",
    "btc15m_shadow_signal_health_": "BTC15M shadow signal health",
    "btc15m_signal_starvation_": "BTC15M frozen signal starvation report",
    "btc15m_f2_live_ws_starvation_": "BTC15M F2 live websocket starvation decomposition",
    "btc15m_postfreeze_replay_refresh_": "BTC15M post-freeze replay/REST official refresh",
    "btc15m_full_causal_replay_refresh_": "BTC15M full-window causal replay/REST official refresh",
    "btc15m_candidate_overlap_": "BTC15M candidate overlap and independence audit",
    "btc15m_frozen_opportunity_rate_": "BTC15M frozen opportunity rate report",
    "btc15m_first_signal_side_semantics_": "BTC15M first-signal side-filter semantics audit",
    "btc15m_shadow_replay_config_audit_": "BTC15M shadow wrapper/replay config audit",
    "btc15m_next_forward_candidate_packet_": "BTC15M next forward candidate preregistration packet",
    "btc_frozen_policy_parity_": "BTC frozen wrapper policy parity audit",
    "btc15m_f2_deployment_gate_refresh_": "BTC15M broad F2 live replay gate",
    "btc15m_live_ws_rest_official_refresh_": "BTC15M live replay REST official fill",
    "btc15m_f2_live_ws_q250_firstskip_rest_official_": "BTC15M q250 first-skip exact live replay REST official fill",
    "btc15m_f2_live_ws_q250_firstskip_causal_rest_official_": "BTC15M q250 first-skip full causal replay REST official fill",
    "btc15m_f2_live_ws_q250_firstskip_yes_causal_rest_official_": "BTC15M q250 YES full causal replay REST official fill",
    "btc15m_f2_live_ws_q250_firstskip_yes_postfreeze_": "BTC15M q250 YES-only post-freeze live replay",
    "btc15m_f2_live_ws_q1000_yes_rest_official_": "BTC15M q1000 YES exact live replay REST official fill",
    "btc15m_f2_live_ws_q1000_yes_causal_rest_official_": "BTC15M q1000 YES full causal replay REST official fill",
    "btc15m_latest_live_replay_diagnostics_": "BTC15M latest live replay diagnostics",
    "btc15m_settlement_basis_watch_": "BTC15M settlement basis watch",
    "btc_settlement_basis_risk_audit_": "BTC settlement basis risk audit",
    "btc_settlement_basis_guard_candidates_": "BTC settlement basis guard candidates",
    "btc_official_settlement_feature_table_": "BTC official settlement canonical feature table",
    "btc_basis_danger_table_": "BTC settlement basis danger table",
    "btc_settlement_basis_model_feasibility_": "BTC settlement basis model feasibility",
    "btc_execution_realism_audit_": "BTC execution-realism audit",
    "btc_ledger_schema_preflight_": "BTC shadow ledger schema preflight",
    "btc_shadow_restart_preflight_": "BTC shadow restart/migration preflight",
    "btc_restart_authorization_packet_": "BTC paper-shadow restart authorization packet",
    "btc_post_restart_verification_": "BTC post-restart evidence-clock verification",
    "btc_post_restart_collection_gate_": "BTC post-restart collection gate",
    "btc_drawdown_sequence_audit_": "BTC official-PnL drawdown sequence audit",
    "btc15m_f2_side_gate_rest_official_refresh_": "BTC15M side-filter official gate",
    "btc15m_predexon_rest_official_": "BTC15M Predexon REST official fill",
    "btc15m_predexon_official_coverage_": "BTC15M Predexon REST-official coverage audit",
    "btc15m_predexon_metadata_vs_rest_": "BTC15M Predexon metadata-vs-REST source fidelity audit",
    "btc15m_predexon_metadata_settlement_": "BTC15M Predexon metadata settlement fill",
    "btc15m_materialized_filter_grid_": "BTC15M materialized first-signal filter grid",
    "btc15m_materialized_fragility_": "BTC15M materialized candidate fragility audit",
    "btc_forward_shadow_status_": "BTC forward shadow status",
    "btc_shadow_official_settlement_": "BTC shadow official settlement audit",
    "btc1h_promotion_gate_audit_": "BTC1H promotion gate audit",
    "btc15m_f2_predexon_jan30_partial_": "BTC15M Jan30 partial Predexon stress",
}

CSV_PRIORITY = [
    "readiness_summary.csv",
    "candidate_triage_summary.csv",
    "gpt_pro_action_checklist.csv",
    "gpt_pro_candidate_status.csv",
    "kill_continue_summary.csv",
    "forward_consistency_summary.csv",
    "row_reconciliation_summary.csv",
    "row_reconciliation_details.csv",
    "btc1h_replay_coverage_summary.csv",
    "btc1h_replay_coverage_rows.csv",
    "focused_readiness.csv",
    "gate_summary.csv",
    "diagnostic_summary.csv",
    "settlement_basis_summary.csv",
    "official_settlement_feature_summary.csv",
    "official_settlement_feature_by_candidate.csv",
    "settlement_basis_risk_gates.csv",
    "settlement_basis_risk_by_candidate.csv",
    "settlement_basis_guard_summary.csv",
    "settlement_basis_guard_promising.csv",
    "basis_danger_by_candidate.csv",
    "basis_danger_bins.csv",
    "basis_guard_deployment_verdict.csv",
    "basis_model_feasibility_summary.csv",
    "basis_model_feature_safety_audit.csv",
    "basis_model_group_summary.csv",
    "execution_realism_summary.csv",
    "execution_realism_field_presence.csv",
    "ledger_schema_preflight_summary.csv",
    "ledger_schema_missing_columns.csv",
    "shadow_restart_preflight_summary.csv",
    "restart_authorization_summary.csv",
    "restart_authorization_checklist.csv",
    "post_restart_verification_checklist.csv",
    "post_restart_target_status.csv",
    "post_restart_collection_gate_summary.csv",
    "drawdown_sequence_summary.csv",
    "drawdown_sequence_details.csv",
    "shadow_signal_health_summary.csv",
    "signal_starvation_summary.csv",
    "f2_live_ws_starvation_summary.csv",
    "f2_live_ws_starvation_gate_counts.csv",
    "f2_live_ws_starvation_near_misses.csv",
    "postfreeze_replay_refresh_summary.csv",
    "full_causal_replay_refresh_summary.csv",
    "candidate_overlap_summary.csv",
    "candidate_pairwise_overlap.csv",
    "candidate_event_membership.csv",
    "detail_family_counts.csv",
    "ttl_outside_distribution.csv",
    "frozen_opportunity_rate_summary.csv",
    "first_signal_side_semantics_summary.csv",
    "first_signal_side_semantics_details.csv",
    "shadow_replay_config_summary.csv",
    "shadow_replay_config_checks.csv",
    "candidate_freeze_specs.csv",
    "candidate_diagnostic_evidence.csv",
    "basis_guard_freeze_specs.csv",
    "basis_guard_diagnostic_evidence.csv",
    "frozen_policy_parity_summary.csv",
    "predexon_official_coverage_summary.csv",
    "predexon_official_coverage_by_window.csv",
    "predexon_metadata_vs_rest_summary.csv",
    "market_result_status_summary.csv",
    "f2_live_ws_summary.csv",
    "summary.csv",
    "side_gate_summary.csv",
    "materialized_filter_summary.csv",
    "summary_by_strategy.csv",
    "combined_evidence.csv",
]

MULTI_CSV_PREFIXES = {
    "btc_forward_row_reconciliation_",
    "btc1h_replay_coverage_audit_",
    "btc_drawdown_sequence_audit_",
    "btc15m_candidate_overlap_",
    "btc15m_next_forward_candidate_packet_",
    "btc_settlement_basis_guard_candidates_",
}

COMPACT_PREFIXES = [
    "deployment_readiness_",
    "btc_gpt_pro_action_status_",
    "btc_forward_evidence_report_",
    "btc_strategy_triage_",
    "btc_evidence_stack_refresh_",
    "btc15m_full_causal_replay_refresh_",
    "btc15m_candidate_overlap_",
    "btc15m_latest_live_replay_diagnostics_",
    "btc15m_first_signal_side_semantics_",
    "btc15m_shadow_replay_config_audit_",
    "btc15m_next_forward_candidate_packet_",
    "btc_settlement_basis_model_feasibility_",
    "btc1h_replay_coverage_audit_",
]


def latest_dir(prefix: str) -> Path | None:
    matches = [p for p in BACKTEST_ROOT.glob(f"{prefix}*") if p.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def read_text_limited(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return f"{head}\n\n...[truncated {len(text) - max_chars} chars]...\n\n{tail}"


def tail_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[-max_chars:] if len(text) > max_chars else text


def table_from_csv(path: Path, max_rows: int = MAX_CSV_ROWS) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:
        return f"(Could not read CSV {path.name}: {exc!r})"
    if not rows:
        return "(empty)"
    fieldnames = list(rows[0].keys())
    keep_rows = rows[:max_rows]
    widths = {name: min(max(len(name), *(len(str(row.get(name, ""))) for row in keep_rows)), 36) for name in fieldnames}

    def fmt(value: object, name: str) -> str:
        s = str(value)
        if len(s) > widths[name]:
            s = s[: max(0, widths[name] - 3)] + "..."
        return s.ljust(widths[name])

    header = " | ".join(fmt(name, name) for name in fieldnames)
    sep = "-+-".join("-" * widths[name] for name in fieldnames)
    body = [" | ".join(fmt(row.get(name, ""), name) for name in fieldnames) for row in keep_rows]
    more = [f"... {len(rows) - max_rows} more rows omitted ..."] if len(rows) > max_rows else []
    return "\n".join([header, sep, *body, *more])


def powershell_process_snapshot() -> str:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*Kalshi-Trading-Bot*' -or $_.CommandLine -like '*btc15m*' -or $_.CommandLine -like '*btc_1hr*' -or $_.CommandLine -like '*predexon*' } | "
        "Select-Object ProcessId,CommandLine | Format-Table -AutoSize | Out-String",
    ]
    try:
        return subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:
        return f"(process snapshot unavailable: {exc!r})"


def git_snapshot() -> str:
    try:
        return subprocess.run(["git", "status", "--short"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:
        return f"(git status unavailable: {exc!r})"


def artifact_sections() -> tuple[str, list[Path]]:
    sections: list[str] = []
    copy_files: list[Path] = []
    for prefix, label in IMPORTANT_PREFIXES.items():
        d = latest_dir(prefix)
        if d is None:
            sections.append(f"## {label}\n\nMissing latest artifact for prefix `{prefix}`.\n")
            continue
        rel = d.relative_to(PROJECT_ROOT)
        sections.append(f"## {label}\n\nArtifact: `{rel}`\n")
        report = d / "report.md"
        if report.exists():
            copy_files.append(report)
            sections.append("### report.md\n\n" + read_text_limited(report, MAX_REPORT_CHARS) + "\n")
        for name in CSV_PRIORITY:
            path = d / name
            if path.exists():
                copy_files.append(path)
                sections.append(f"### {name}\n\n```text\n{table_from_csv(path)}\n```\n")
                if prefix not in MULTI_CSV_PREFIXES:
                    break
        run_info = d / "run_info.json"
        if run_info.exists():
            copy_files.append(run_info)
            sections.append("### run_info.json\n\n```json\n" + read_text_limited(run_info, 8_000) + "\n```\n")
    return "\n".join(sections), copy_files


def compact_artifact_sections() -> tuple[str, list[Path]]:
    sections: list[str] = []
    copy_files: list[Path] = []
    for prefix in COMPACT_PREFIXES:
        label = IMPORTANT_PREFIXES[prefix]
        d = latest_dir(prefix)
        if d is None:
            sections.append(f"## {label}\n\nMissing latest artifact for prefix `{prefix}`.\n")
            continue
        rel = d.relative_to(PROJECT_ROOT)
        sections.append(f"## {label}\n\nArtifact: `{rel}`\n")
        report = d / "report.md"
        if report.exists():
            copy_files.append(report)
            sections.append("### report.md\n\n" + read_text_limited(report, COMPACT_REPORT_CHARS) + "\n")
        csv_count = 0
        for name in CSV_PRIORITY:
            path = d / name
            if not path.exists():
                continue
            copy_files.append(path)
            sections.append(f"### {name}\n\n```text\n{table_from_csv(path, COMPACT_CSV_ROWS)}\n```\n")
            csv_count += 1
            if csv_count >= (2 if prefix in {"btc15m_next_forward_candidate_packet_"} else 1):
                break
        run_info = d / "run_info.json"
        if run_info.exists():
            copy_files.append(run_info)
            sections.append("### run_info.json\n\n```json\n" + read_text_limited(run_info, 3_000) + "\n```\n")
    return "\n".join(sections), copy_files


def write_copy_helper(packet_dir: Path, prompt_path: Path) -> None:
    helper = packet_dir / "copy_prompt_to_clipboard.ps1"
    helper.write_text(
        f"Get-Content -Raw -LiteralPath {json.dumps(str(prompt_path))} | Set-Clipboard\n"
        "Write-Host 'Copied GPT Pro prompt to clipboard.'\n",
        encoding="utf-8",
    )


def copy_to_clipboard(path: Path) -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Get-Content -Raw -LiteralPath {json.dumps(str(path))} | Set-Clipboard",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=30,
    )


def open_chatgpt() -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Start-Process 'https://chatgpt.com/'"],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=30,
    )


def prompt_text(evidence_bundle_name: str) -> str:
    return textwrap.dedent(
        f"""
        You are acting as a senior quantitative research lead and production trading risk auditor.

        We are building algorithmic Kalshi BTC short-horizon prediction-market strategies. The current objective is:

        > Find or validate a truly deployable return-generating Kalshi BTC short-horizon strategy, prioritizing BTC15M and then BTC1H.

        I am attaching `{evidence_bundle_name}`, which contains our current research ledger tail, readiness checks, backtest summaries, and live/paper process state. Treat it as the source of truth. Do not assume proxy backtests are enough.

        Non-negotiable constraints:

        1. Do not recommend deployment unless the strategy survives official Kalshi settlement, fees, realistic top-of-book/FOK execution, one-trade-per-event rules, and enough forward/live evidence.
        2. Distinguish these data sources:
           - Live websocket capture: most faithful; final promotion gate.
           - Predexon historical orderbook snapshots: useful for research/training, but not a perfect live receive-time replay.
           - Kalshi historical candles/CSVs: broad research only; often optimistic.
        3. Avoid leakage. No future BTC close, future settlement, future orderbook state, or best-row-after-the-fact selection.
        4. Kalshi BTC15M settles on CF Benchmarks RTI/BRTI style official expiration value, not necessarily Coinbase/Kraken spot. Proxy settlement has already produced false positives.
        5. Our latest readiness checker found production_ready_count = 0. If you disagree, explain exactly which gate evidence supports deployment.
        6. We care about real deployment decisions: expected return, max drawdown, sample size, capacity, operational risk, and what to run next.

        Please produce:

        1. A blunt audit of whether anything is deployable now.
        2. The top 3 strategy/research paths ranked by probability of becoming deployable soon.
        3. A concrete 24-hour action plan with exact analyses/backtests we should run next.
        4. A concrete 7-day research plan.
        5. A promotion gate checklist with numeric thresholds.
        6. Any model improvements we should prioritize, especially around official settlement/reference-price mismatch.
        7. A list of traps/false-positive patterns you see in the evidence.
        8. If you need more data, name the exact artifact/table/time range and why.

        Be skeptical and specific. Do not give generic trading advice. Tie every recommendation to the evidence in the attached packet.
        """
    ).strip() + "\n"


def browser_safe_prompt_text(compact_evidence: str) -> str:
    return (
        prompt_text("the inline compact evidence bundle below")
        + "\n---\n\n# Inline Compact Evidence Bundle\n\n"
        + compact_evidence
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a GPT Pro strategy-advisor prompt/evidence packet.")
    p.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    p.add_argument("--openai-model-note", default="Use ChatGPT Pro's strongest reasoning model or Deep Research mode if available.")
    p.add_argument("--copy", action="store_true", help="Copy prompt.md to the Windows clipboard after building the packet.")
    p.add_argument(
        "--copy-browser-safe",
        action="store_true",
        help="Copy browser_safe_prompt.md, an inline compact packet intended for direct ChatGPT paste.",
    )
    p.add_argument("--open-chatgpt", action="store_true", help="Open chatgpt.com after building the packet.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    packet_dir = args.out_root / f"strategy_advisor_{ts}"
    packet_dir.mkdir(parents=True, exist_ok=True)

    artifacts, copy_files = artifact_sections()
    compact_artifacts, compact_copy_files = compact_artifact_sections()
    ledger = tail_text(PROJECT_ROOT / "docs" / "research_ledger.md", MAX_LEDGER_CHARS)
    compact_ledger = tail_text(PROJECT_ROOT / "docs" / "research_ledger.md", COMPACT_LEDGER_CHARS)
    process_snapshot = powershell_process_snapshot()
    git = git_snapshot()

    evidence = textwrap.dedent(
        f"""
        # Kalshi BTC Strategy Evidence Bundle

        Created UTC: `{datetime.now(timezone.utc).isoformat()}`

        ## How To Use This Packet

        Paste `prompt.md` into ChatGPT Pro and upload this evidence bundle. If the chat UI accepts ZIP files, upload the ZIP too; otherwise upload/paste `evidence_bundle.md`.

        {args.openai_model_note}

        ## Current Process Snapshot

        ```text
        {process_snapshot}
        ```

        ## Git Status Snapshot

        ```text
        {git}
        ```

        ## Research Ledger Tail

        ```text
        {ledger}
        ```

        # Latest Artifacts

        {artifacts}
        """
    ).strip() + "\n"

    evidence_path = packet_dir / "evidence_bundle.md"
    evidence_path.write_text(evidence, encoding="utf-8")
    prompt_path = packet_dir / "prompt.md"
    prompt_path.write_text(prompt_text(evidence_path.name), encoding="utf-8")
    compact_evidence = textwrap.dedent(
        f"""
        # Kalshi BTC Strategy Compact Evidence Bundle

        Created UTC: `{datetime.now(timezone.utc).isoformat()}`

        This browser-safe bundle is intentionally smaller than `evidence_bundle.md`.
        It is for direct paste into ChatGPT Pro when browser automation cannot
        upload the ZIP or paste the full evidence file. Use `evidence_bundle.md`
        or the ZIP for deeper review when the UI permits uploads.

        {args.openai_model_note}

        ## Current Process Snapshot

        ```text
        {process_snapshot}
        ```

        ## Git Status Snapshot

        ```text
        {git}
        ```

        ## Research Ledger Tail

        ```text
        {compact_ledger}
        ```

        # Highest-Signal Latest Artifacts

        {compact_artifacts}
        """
    ).strip() + "\n"
    compact_evidence_path = packet_dir / "evidence_bundle_compact.md"
    compact_evidence_path.write_text(compact_evidence, encoding="utf-8")
    browser_safe_path = packet_dir / "browser_safe_prompt.md"
    browser_safe_path.write_text(browser_safe_prompt_text(compact_evidence), encoding="utf-8")
    write_copy_helper(packet_dir, prompt_path)

    zip_path = packet_dir.with_suffix(".zip")

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "packet_dir": str(packet_dir),
        "prompt": str(prompt_path),
        "browser_safe_prompt": str(browser_safe_path),
        "evidence_bundle": str(evidence_path),
        "evidence_bundle_compact": str(compact_evidence_path),
        "zip": str(zip_path),
        "source_files_included_by_reference": sorted(
            {str(p.relative_to(PROJECT_ROOT)) for p in [*copy_files, *compact_copy_files] if p.exists()}
        ),
        "excluded": [
            "credentials.env",
            "raw DuckDB files",
            "raw Parquet orderbook data",
            "full logs",
        ],
    }
    manifest_path = packet_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in [
            prompt_path,
            evidence_path,
            compact_evidence_path,
            browser_safe_path,
            manifest_path,
            packet_dir / "copy_prompt_to_clipboard.ps1",
        ]:
            zf.write(path, path.name)

    latest_path = args.out_root / "latest_strategy_advisor_packet.json"
    latest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    if args.copy:
        copy_to_clipboard(prompt_path)
    if args.copy_browser_safe:
        copy_to_clipboard(browser_safe_path)
    if args.open_chatgpt:
        open_chatgpt()

    print(f"Packet: {packet_dir}")
    print(f"Prompt: {prompt_path}")
    print(f"Browser-safe prompt: {browser_safe_path}")
    print(f"Evidence: {evidence_path}")
    print(f"Compact evidence: {compact_evidence_path}")
    print(f"Zip: {zip_path}")
    print(f"Latest manifest: {latest_path}")
    print("\nNext:")
    if not args.copy:
        print(f"  powershell -ExecutionPolicy Bypass -File {packet_dir / 'copy_prompt_to_clipboard.ps1'}")
    print("  Open ChatGPT Pro, paste the prompt, upload the ZIP or evidence_bundle.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
