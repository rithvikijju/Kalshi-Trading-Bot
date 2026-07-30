"""
Quick auth tester — verify TopstepX/ProjectX credentials without launching the bot.

    source topstep_bot/.env.sh
    .venv/bin/python -m topstep_bot.auth_test                 # uses env vars
    .venv/bin/python -m topstep_bot.auth_test MyProjectXUser  # override username only

On success it prints the account list so you can confirm TOPSTEP_ACCOUNT_NAME.
"""
import json
import os
import sys

import httpx

from .config import API_BASE


def main():
    user = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TOPSTEP_USERNAME", "")
    key = os.getenv("TOPSTEP_API_KEY", "")
    print(f"endpoint : {API_BASE}/api/Auth/loginKey")
    print(f"userName : {user!r}")
    print(f"apiKey   : len={len(key)} tail=...{key[-4:] if key else ''}")
    if not user or not key:
        print("→ missing username or key; `source topstep_bot/.env.sh` first.")
        return

    r = httpx.post(f"{API_BASE}/api/Auth/loginKey",
                   json={"userName": user, "apiKey": key}, timeout=20)
    d = r.json()
    print(f"HTTP {r.status_code} | success={d.get('success')} errorCode={d.get('errorCode')}")
    if not d.get("success"):
        print("✗ auth rejected. errorCode 3 = bad credentials: the userName must be your "
              "PROJECTX username from the linking step (not TopstepX login / email), and the "
              "ProjectX API Access subscription must be active.")
        return

    token = d["token"]
    print(f"✓ authenticated (token len {len(token)})")
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    a = httpx.post(f"{API_BASE}/api/Account/search", json={"onlyActiveAccounts": True},
                   headers=hdr, timeout=20).json()
    accts = a.get("accounts", [])
    print(f"accounts ({len(accts)}):")
    for x in accts:
        print(f"  id={x.get('id')}  name={x.get('name')!r}  "
              f"balance={x.get('balance')}  canTrade={x.get('canTrade')}  "
              f"simulated={x.get('simulated')}")
    print("→ set TOPSTEP_ACCOUNT_NAME to the name above you want to trade (or leave blank "
          "for the first active one).")


if __name__ == "__main__":
    main()
