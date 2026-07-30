"""
Historical backfill — build a real dataset by stitching multiple contract months.

The active contract only carries a few weeks of life, so to get months of 1-min bars we:
  1. resolve the active contract id (e.g. CON.F.US.MNQ.U26),
  2. enumerate prior expired months (quarterly for index, monthly for crypto),
  3. paginate /History/retrieveBars backward (20k bars/request) for each,
  4. stitch into one continuous per-instrument series, dedup by timestamp.

Stored in topstep_bot/data/history.duckdb (table hist_bars). This is a deliberate
simplification: we do NOT back-adjust for roll gaps — for learning intraday 1-min
patterns the model normalizes each window locally, so the absolute level/basis doesn't
matter. (Documented so it's not mistaken for a continuous adjusted series.)

    source topstep_bot/.env.sh
    .venv/bin/python -m topstep_bot.ml.history --days 180
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

import duckdb
import httpx

from ..config import API_BASE, BotConfig, INSTRUMENTS

# futures month codes
_CODES = "FGHJKLMNQUVXZ"   # Jan..Dec (standard CME letters, excl. I/O/etc.)
_CODE_TO_MONTH = {c: i + 1 for i, c in enumerate("FGHJKMNQUVXZ")}  # 12 listing months
_MONTH_TO_CODE = {v: k for k, v in _CODE_TO_MONTH.items()}
_QUARTERLY = {3: "H", 6: "M", 9: "U", 12: "Z"}


def _parse_id(contract_id: str):
    """'CON.F.US.MNQ.U26' -> ('CON.F.US.MNQ', 'U', 26)."""
    root, suffix = contract_id.rsplit(".", 1)
    return root, suffix[0], int(suffix[1:])


def _make_id(root: str, code: str, yy: int) -> str:
    return f"{root}.{code}{yy:02d}"


def prior_contracts(active_id: str, n: int, quarterly: bool) -> list[str]:
    """Active id + `n` prior contract ids, newest→oldest."""
    root, code, yy = _parse_id(active_id)
    month = _CODE_TO_MONTH[code]
    months = sorted(_QUARTERLY) if quarterly else list(range(1, 13))
    ids = [active_id]
    cur_m, cur_y = month, yy
    for _ in range(n):
        # step back to the previous listing month
        earlier = [m for m in months if m < cur_m]
        if earlier:
            cur_m = earlier[-1]
        else:
            cur_m = months[-1]
            cur_y -= 1
        code = _QUARTERLY[cur_m] if quarterly else _MONTH_TO_CODE[cur_m]
        ids.append(_make_id(root, code, cur_y))
    return ids


class _Api:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.token = None
        self.http = httpx.Client(base_url=API_BASE, timeout=60.0)

    def auth(self):
        d = self.http.post("/api/Auth/loginKey",
                           json={"userName": self.cfg.username, "apiKey": self.cfg.api_key}).json()
        if not d.get("success"):
            raise RuntimeError(f"auth failed errorCode={d.get('errorCode')}")
        self.token = d["token"]

    def post(self, path, body):
        return self.http.post(path, json=body,
                              headers={"Authorization": f"Bearer {self.token}"}).json()

    def active_contract(self, search: str) -> str | None:
        d = self.post("/api/Contract/search", {"searchText": search, "live": False})
        cs = d.get("contracts", [])
        pick = next((c for c in cs if c.get("activeContract")), None) or (cs[0] if cs else None)
        return str(pick["id"]) if pick else None


def _pull_contract(api: _Api, cid: str, not_before: datetime, max_chunks: int = 30) -> list[dict]:
    """Paginate retrieveBars backward for one contract until empty or past not_before."""
    out: list[dict] = []
    end = datetime.now(timezone.utc)
    for _ in range(max_chunks):
        start = end - timedelta(days=14)         # ~20k 1-min bars per chunk
        if start < not_before:
            start = not_before
        d = api.post("/api/History/retrieveBars", {
            "contractId": cid, "live": False,
            "startTime": start.isoformat(), "endTime": end.isoformat(),
            "unit": 2, "unitNumber": 1, "limit": 20000, "includePartialBar": False})
        bars = d.get("bars", [])
        if not bars:
            break
        out.extend(bars)
        oldest = min(datetime.fromisoformat(b["t"].replace("Z", "+00:00")) for b in bars)
        if oldest <= not_before or start <= not_before:
            break
        end = oldest - timedelta(seconds=1)
        time.sleep(0.2)                          # be polite to the API
    return out


def backfill(cfg: BotConfig, instruments: list[str], days: int) -> dict:
    api = _Api(cfg)
    api.auth()
    con = duckdb.connect(cfg.db_path.replace("capture.duckdb", "history.duckdb"))
    con.execute("""CREATE TABLE IF NOT EXISTS hist_bars(
        instrument VARCHAR, ts DOUBLE, o DOUBLE, h DOUBLE, l DOUBLE, c DOUBLE, v DOUBLE,
        PRIMARY KEY (instrument, ts))""")
    not_before = datetime.now(timezone.utc) - timedelta(days=days)
    summary = {}
    for key in instruments:
        inst = INSTRUMENTS[key]
        active = api.active_contract(inst.search)
        if not active:
            summary[key] = "no contract"
            continue
        quarterly = inst.kind == "index"
        # enough prior contracts to span `days` (quarterly ≈ 90d each, monthly ≈ 30d)
        n = max(1, days // (80 if quarterly else 28) + 1)
        ids = prior_contracts(active, n, quarterly)
        seen: dict[float, tuple] = {}
        for cid in ids:
            try:
                bars = _pull_contract(api, cid, not_before)
            except Exception as e:
                print(f"  {key} {cid}: {e}")
                continue
            for b in bars:
                ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp()
                if ts < not_before.timestamp():
                    continue
                # on roll overlap, keep the first (newer-contract) bar we saw for that ts
                seen.setdefault(ts, (b["o"], b["h"], b["l"], b["c"], b.get("v", 0.0)))
            print(f"  {key} {cid}: pulled {len(bars)} bars (running unique {len(seen)})")
        rows = [(key, ts, *v) for ts, v in seen.items()]
        if rows:
            con.executemany("INSERT OR REPLACE INTO hist_bars VALUES (?,?,?,?,?,?,?)", rows)
        summary[key] = len(rows)
    # merge any live-captured bars too (they extend the series to 'now')
    try:
        con.execute(f"ATTACH '{cfg.db_path}' AS cap (READ_ONLY)")
        con.execute("""INSERT OR REPLACE INTO hist_bars
                       SELECT instrument, ts, o, h, l, c, v FROM cap.bars""")
        con.execute("DETACH cap")
    except Exception:
        pass
    con.close()
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--instruments", nargs="*", default=None)
    args = ap.parse_args()
    cfg = BotConfig()
    insts = args.instruments or [i.key for i in cfg.enabled_instruments()]
    print(f"backfilling {insts} for ~{args.days} days...")
    s = backfill(cfg, insts, args.days)
    print("done:", s)


if __name__ == "__main__":
    main()
