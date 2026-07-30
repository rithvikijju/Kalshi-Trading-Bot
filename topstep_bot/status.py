"""
Check the bot's live progress without touching the capture DB it's writing to.

Reads the heartbeat the running bot writes every 30s (topstep_bot/data/status.json).

    .venv/bin/python -m topstep_bot.status
"""
import json
from pathlib import Path

# microstructure dataset needs ~N_BUCKETS*TRADES_PER_BUCKET + buffer trades to start
from .ml.microstructure import N_BUCKETS, TRADES_PER_BUCKET
MIN_TAPE = N_BUCKETS * TRADES_PER_BUCKET + 100
GOOD_TAPE = 40_000          # rough "enough for a meaningful walk-forward" per instrument


def main():
    p = Path("topstep_bot/data/status.json")
    if not p.exists():
        print("no status yet — start the bot: python -m topstep_bot.run --mode sim --no-discord")
        return
    d = json.loads(p.read_text())
    print(f"=== TopStep ICT bot — as of {d['updated']} ===")
    print(f"mode={d['mode']} model={d['model']} "
          f"{'PAUSED ' if d['paused'] else ''}{'HALTED' if d['halted'] else 'running'}")
    r = d["risk"]
    print(f"\nRISK: equity ${r['equity']:.0f} | floor ${r['floor']:.0f} | day PnL ${r['day_pnl']:+.0f}"
          f" | target left ${r['target_remaining']:.0f}")
    pos = d.get("positions") or {}
    print(f"positions: {pos or 'flat'} | unrealized ${d.get('unrealized', 0):+.2f}")

    print("\nDATA CAPTURED (the order-flow dataset being built):")
    cap = d.get("capture", {})
    for inst, c in cap.items():
        tape = c["tape"]
        if tape < MIN_TAPE:
            state = f"warming up ({tape}/{MIN_TAPE} trades to even start)"
        elif tape < GOOD_TAPE:
            state = f"building — {tape}/{GOOD_TAPE} for a meaningful walk-forward ({tape/GOOD_TAPE:.0%})"
        else:
            state = f"READY — {tape} trades, run the microstructure trainer"
        print(f"  {inst}: {tape:>7} tape | {c['book']:>6} book | {c['bars']:>5} bars  → {state}")

    ready = [i for i, c in cap.items() if c["tape"] >= MIN_TAPE]
    print("\nNEXT:")
    if ready:
        print(f"  enough tape to test: .venv/bin/python -m topstep_bot.ml.microstructure "
              f"--instrument {ready[0]}")
    else:
        print("  keep the bot running (esp. 9:30am-4pm ET) to accumulate order flow.")


if __name__ == "__main__":
    main()
