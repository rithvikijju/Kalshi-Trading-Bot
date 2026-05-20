# GPT Pro Review Workflow

This repo uses GPT Pro as a plan reviewer, not as the executor. Codex builds a
compact evidence bundle, GPT Pro reviews the strategy/research plan, then Codex
implements or backtests the revised plan.

## One-Command Packet

Build the packet:

```powershell
.\scripts\ask_gpt_pro_strategy.ps1
```

Build it and copy the prompt to the clipboard:

```powershell
.\scripts\ask_gpt_pro_strategy.ps1 -Copy
```

Build it, copy the prompt, and open ChatGPT:

```powershell
.\scripts\ask_gpt_pro_strategy.ps1 -Copy -OpenChatGPT
```

Build a smaller inline packet for direct browser paste when upload or very long
paste automation is brittle:

```powershell
.\scripts\ask_gpt_pro_strategy.ps1 -CopyBrowserSafe -OpenChatGPT
```

The script writes:

- `gpt_pro_packets\latest_strategy_advisor_packet.json`
- `gpt_pro_packets\strategy_advisor_<timestamp>\prompt.md`
- `gpt_pro_packets\strategy_advisor_<timestamp>\evidence_bundle.md`
- `gpt_pro_packets\strategy_advisor_<timestamp>\evidence_bundle_compact.md`
- `gpt_pro_packets\strategy_advisor_<timestamp>\browser_safe_prompt.md`
- `gpt_pro_packets\strategy_advisor_<timestamp>.zip`

Upload the ZIP or `evidence_bundle.md` to ChatGPT Pro, paste the prompt, and
select the strongest Pro reasoning model available.

After GPT Pro finishes, copy its answer and save it back into this repo:

```powershell
.\scripts\save_gpt_pro_review.ps1 -Name strategy_advisor
```

## Why This Is Our Oracle Step

This is the same split described in the GPT Pro workflow article:

1. Package context into one focused markdown bundle.
2. Send it to GPT Pro for a slow, high-quality plan review.
3. Bring the response back into Codex for implementation and verification.

The packet builder deliberately includes current readiness gates, research
ledger tail, key backtest tables, and running-process state. It deliberately
excludes:

- `credentials.env`
- raw DuckDB databases
- raw Parquet orderbook files
- full logs

## Current Browser Step

As of the 2026-05-17 Codex run, the Chrome extension backend is reachable from
Codex through the Browser plugin runtime even when tool discovery does not show
a dedicated `chrome` namespace. The connected Chrome profile was `Sami`, and
ChatGPT was logged in with a visible `Pro` composer.

Known blocker: on some Windows Codex installs, the Chrome plugin is not
discoverable in the Plugins panel even though the Chrome extension docs say it
should be added from Codex -> Plugins. In that case, installing the Chrome Web
Store extension alone is not enough; Codex also has to expose the Chrome plugin
surface. Track this symptom here:

```text
https://github.com/openai/codex/issues/22400
```

If Chrome is not visible in normal tool discovery, first ask the Browser
runtime for connected browsers and look for a client with `type = extension`
and `name = Chrome`. Only fall back to manual paste if that backend is absent
or ChatGPT is not logged in.

Preferred automation order:

1. **Codex Chrome plugin**: preferred. It should let Codex use your existing
   logged-in Chrome session and work in a background tab.
2. **Computer Use / desktop control**: acceptable fallback, but it will take
   over Chrome while running.
3. **Browser Use in Codex**: fallback only; it requires a separate ChatGPT web
   login and has been less reliable.

The handoff should do this automatically:

1. Run `.\scripts\ask_gpt_pro_strategy.ps1 -Copy`.
2. Open ChatGPT.
3. Select GPT Pro / Pro thinking.
4. Paste `prompt.md` plus the evidence bundle, or upload
   `strategy_advisor_<timestamp>.zip` if file upload automation is reliable.
   ChatGPT may convert a long paste into a pasted-text attachment; this is okay
   if the send button becomes enabled.
5. If the Chrome extension pipe breaks on the full bundle, rerun with
   `-CopyBrowserSafe` and paste `browser_safe_prompt.md` directly. This compact
   packet preserves the decisive deployability artifacts while keeping the
   payload small enough for normal chat paste.
6. Submit and wait.
7. Save GPT Pro's response into `docs\gpt_pro_reviews\`.
8. Codex reads the response and converts it into a concrete implementation or
   research checklist.

## What To Ask GPT Pro For

The current prompt asks for:

- blunt deployability audit
- top three paths most likely to become deployable
- 24-hour action plan
- 7-day research plan
- numeric promotion gate
- settlement/reference-price model improvements
- false-positive traps
- exact data requests

This is intentional. GPT Pro should be skeptical and should not greenlight a
strategy from proxy PnL alone.
