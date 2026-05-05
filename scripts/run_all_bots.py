#!/usr/bin/env python3
import subprocess
import sys
import time

scripts = [
    ("Base Paper Bot", "scripts/btc_1hr_paper.py"),
    ("Hardened Bot", "scripts/btc_1hr_hardened.py"),
    ("Neo-Hardened Bot", "scripts/btc_1hr_neohardened.py")
]

procs = []
print("==================================================")
print("   STARTING KALSHI BTC A/B/C TESTING SUITE")
print("==================================================")

for name, script in scripts:
    print(f"[*] Starting {name} ({script})...")
    # Popen will stream output to the terminal, but the scripts also log to files.
    p = subprocess.Popen([sys.executable, script])
    procs.append((name, p))
    time.sleep(2) # Stagger starts slightly so their fetching doesn't hit rate limits identically

print("\n[+] All 3 bots are now running in parallel!")
print("[!] Press Ctrl+C to stop all bots gracefully.\n")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n\n[-] KeyboardInterrupt received. Stopping all bots...")
    for name, p in procs:
        print(f"    Terminating {name}...")
        p.terminate()
    for name, p in procs:
        p.wait()
    print("[+] All bots stopped successfully.")
