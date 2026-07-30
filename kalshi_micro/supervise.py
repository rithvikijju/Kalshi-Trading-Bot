"""
Supervisor — keep the rich capture alive across crashes/network drops (it kept dying
overnight with no restart). Relaunches the capture if it exits, with backoff.

    python -m kalshi_micro.supervise --venue perp --ticker KXBTCPERP --hz 1 --rich
"""
from __future__ import annotations

import subprocess
import sys
import time


def main():
    args = sys.argv[1:]
    cmd = [sys.executable, "-u", "-m", "kalshi_micro.capture"] + args
    backoff = 2
    n = 0
    while True:
        n += 1
        print(f"[supervise] launch #{n}: {' '.join(cmd)}", flush=True)
        t0 = time.time()
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("[supervise] stopped by user", flush=True)
            return
        ran = time.time() - t0
        backoff = 2 if ran > 60 else min(backoff * 2, 60)   # reset if it ran a while
        print(f"[supervise] capture exited after {ran:.0f}s — restarting in {backoff}s", flush=True)
        time.sleep(backoff)


if __name__ == "__main__":
    main()
