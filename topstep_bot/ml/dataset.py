"""
Build the supervised dataset: (price-pattern sequence + ICT features) → win/loss.

We replay the SAME signal engine over the stitched history. Every setup it fires becomes
one labelled example:
  * X_seq  : the last SEQ_LEN bars of price action, normalized scale-free (OHLC as ATR
             multiples relative to the entry, volume vs its local mean) — what the CNN
             learns temporal patterns from.
  * X_feat : the 24-dim engineered ICT feature vector (the same one the logistic uses).
  * y      : 1 if the trade would net a profit (target before stop, after round-turn fee),
             else 0 — labelled by walking the real bars forward, stop checked first.
  * pnl,ts : per-contract net PnL (for expectancy) and timestamp (for the temporal split).

This is "test the strategy against the patterns in the data": the model is trained to tell
winning setups from losing ones, given the pattern that led to them.
"""
from __future__ import annotations

import numpy as np

from ..backtest import walk_exit
from ..broker.base import Bar, Side
from ..config import INSTRUMENTS
from .features import vector
from ..signals.engine import SignalEngine
from ..signals.structure import atr

SEQ_LEN = 64        # bars of price action fed to the sequence model
WINDOW = 150        # context bars handed to the signal engine
N_CHAN = 5          # o,h,l,c (ATR units vs entry) + volume (vs local mean)


def _normalize_seq(bars: list[Bar], ref: float, a: float) -> np.ndarray:
    """(SEQ_LEN, N_CHAN) scale-free representation."""
    a = a if a > 0 else 1.0
    vols = np.array([b.v for b in bars], dtype=float)
    vmean = vols.mean() if vols.mean() > 0 else 1.0
    out = np.zeros((len(bars), N_CHAN), dtype=np.float32)
    for i, b in enumerate(bars):
        out[i, 0] = (b.o - ref) / a
        out[i, 1] = (b.h - ref) / a
        out[i, 2] = (b.l - ref) / a
        out[i, 3] = (b.c - ref) / a
        out[i, 4] = np.log1p(b.v) - np.log1p(vmean)
    return out


def resample(bars: list[Bar], tf_min: int) -> list[Bar]:
    """Aggregate 1-min bars into tf_min-minute bars (bucket by floor of ts)."""
    if tf_min <= 1:
        return bars
    span = tf_min * 60
    out: list[Bar] = []
    cur = None
    for b in bars:
        bucket = (int(b.ts) // span) * span
        if cur is None or bucket > cur.ts:
            if cur is not None:
                out.append(cur)
            cur = Bar(b.instrument, bucket, b.o, b.h, b.l, b.c, b.v)
        else:
            cur.h = max(cur.h, b.h); cur.l = min(cur.l, b.l)
            cur.c = b.c; cur.v += b.v
    if cur is not None:
        out.append(cur)
    return out


def build_for_instrument(con, instrument: str, min_rr: float = 0.0,
                         tf_min: int = 1, min_target_r: float = 0.0) -> dict:
    rows = con.execute(
        "SELECT instrument, ts, o, h, l, c, v FROM hist_bars WHERE instrument=? ORDER BY ts",
        [instrument]).fetchall()
    bars = resample([Bar(*r) for r in rows], tf_min)
    inst = INSTRUMENTS[instrument]
    dpp = inst.tick_value / inst.tick_size
    engine = SignalEngine(min_bars=max(60, WINDOW // 2), min_target_r=min_target_r)

    Xs, Xf, ys, pnls, tss, rrs = [], [], [], [], [], []
    for i in range(WINDOW, len(bars) - 1):
        w = bars[i - WINDOW + 1:i + 1]
        setup = engine.generate(instrument, w)
        if setup is None or setup.rr < min_rr:
            continue
        a = atr(w, 14)
        if a <= 0 or i - SEQ_LEN + 1 < 0:
            continue
        exit_px, _ = walk_exit(bars, i, setup)
        pnl = (exit_px - setup.entry) * setup.direction.sign * dpp - inst.rt_cost
        Xs.append(_normalize_seq(bars[i - SEQ_LEN + 1:i + 1], setup.entry, a))
        Xf.append(vector(setup))
        ys.append(1 if pnl > 0 else 0)
        pnls.append(pnl)
        tss.append(setup.ts)
        rrs.append(setup.rr)

    if not ys:
        return dict(X_seq=np.empty((0, SEQ_LEN, N_CHAN), np.float32),
                    X_feat=np.empty((0, len(vector_dummy())), np.float32),
                    y=np.empty(0), pnl=np.empty(0), ts=np.empty(0), rr=np.empty(0))
    return dict(X_seq=np.array(Xs, np.float32), X_feat=np.array(Xf, np.float32),
                y=np.array(ys, np.int64), pnl=np.array(pnls, np.float64),
                ts=np.array(tss, np.float64), rr=np.array(rrs, np.float64))


def vector_dummy():
    from .features import FEATURE_NAMES
    return FEATURE_NAMES


def build_all(history_db: str, instruments: list[str], min_rr: float = 0.0,
              cache: str | None = None, tf_min: int = 1, min_target_r: float = 0.0) -> dict:
    """Build + concatenate examples across instruments, sorted by time (for walk-forward)."""
    import duckdb
    con = duckdb.connect(history_db, read_only=True)
    parts = [build_for_instrument(con, k, min_rr, tf_min, min_target_r) for k in instruments]
    con.close()
    parts = [p for p in parts if len(p["y"]) > 0]
    if not parts:
        raise RuntimeError("no examples built — is history.duckdb populated? run ml.history first")
    out = {k: np.concatenate([p[k] for p in parts], axis=0)
           for k in ("X_seq", "X_feat", "y", "pnl", "ts", "rr")}
    order = np.argsort(out["ts"])               # global temporal order
    out = {k: v[order] for k, v in out.items()}
    if cache:
        np.savez_compressed(cache, **out)
    return out
