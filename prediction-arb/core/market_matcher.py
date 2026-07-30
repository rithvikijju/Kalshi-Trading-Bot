"""Cross-platform market matcher.

Layered matching (fast → slow):
  1. Manual override table for known high-volume pairs
  2. Exact / near-exact title match (rapidfuzz.token_set_ratio)
  3. Fuzzy match on title + description
  4. (Optional) sentence-transformers semantic similarity — heavy import,
     off by default

Output is a list[MatchedMarketPair] with confidence scores.
Verified matches are persisted to a SQLite cache so future runs reuse them.
"""
from __future__ import annotations
import json, re, sys, sqlite3, time
from datetime import timedelta
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.models import NormalizedMarket, MatchedMarketPair, MatchMethod, Platform
from utils.logger import setup_logger

log = setup_logger("matcher")


# ─── Strike extraction ──────────────────────────────────────────────
# Kalshi BTC daily: KXBTCD-26MAY1815-T87299.99  → strike 87299.99
# Polymarket: "Will the price of Bitcoin be above $70,000 on May 18?" → 70000
_KALSHI_STRIKE_RE = re.compile(r'-T(\d+(?:\.\d+)?)$')
_POLY_DOLLAR_RE   = re.compile(r'\$(\d[\d,]*\.?\d*)\s*(?:k|K|thousand)?\b')


def kalshi_strike(market: NormalizedMarket) -> Optional[float]:
    """Extract numeric strike from Kalshi market_id, if any."""
    m = _KALSHI_STRIKE_RE.search(market.market_id or "")
    if not m: return None
    try: return float(m.group(1))
    except ValueError: return None


def polymarket_strike(market: NormalizedMarket) -> Optional[float]:
    """Extract dollar strike from Polymarket title, e.g. "$70,000" or "$150k"."""
    title = market.title or ""
    m = _POLY_DOLLAR_RE.search(title)
    if not m: return None
    raw = m.group(1).replace(",", "")
    try:
        v = float(raw)
    except ValueError:
        return None
    # Handle "150k" / "150K"
    if re.search(r'\$\d[\d,]*\.?\d*\s*[kK]\b', title): v *= 1000
    return v


def underlying_key(market: NormalizedMarket) -> str:
    """A coarse 'event family' key — e.g. 'btc_2026-05-18', 'eth_2026-05-18',
    'fed_2026-06'. Used to group strike-ladders so we only match within a family.
    """
    title = (market.title or "").lower()
    mid = (market.market_id or "").lower()
    asset = "other"
    for k in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol"):
        if k in title or k in mid:
            asset = "btc" if k in ("bitcoin", "btc") else \
                    "eth" if k in ("ethereum", "eth") else "sol"
            break
    if "fed" in title or "fed" in mid or "rate" in title:
        asset = "fed"
    if "cpi" in title or "cpi" in mid: asset = "cpi"
    date_key = ""
    if market.resolution_date:
        date_key = market.resolution_date.strftime("%Y-%m-%d")
    return f"{asset}_{date_key}"

# ─── Manual mapping table for known pairs ────────────────────────────
# Keys are kalshi tickers, values are polymarket condition_ids OR slug substrings.
# Maintain this as you verify pairs in the matching notebook.
MANUAL_PAIRS: dict[str, str] = {
    # "KXPRES-2028-DJT": "presidential-election-winner-2028-trump",
    # "FED-26JUN-25BP-CUT": "fed-rate-decision-june-2026-25bp-cut",
}


class MatchCache:
    """SQLite-backed cache of verified matches (or rejected pairs)."""
    def __init__(self, path: str = "data/cache/matches_cache.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.path)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            kalshi_id TEXT NOT NULL,
            polymarket_id TEXT NOT NULL,
            confidence REAL,
            method TEXT,
            verified INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0,
            notes TEXT,
            updated_at TEXT,
            PRIMARY KEY (kalshi_id, polymarket_id)
        )""")
        conn.commit(); conn.close()

    def get_verified(self) -> dict[str, str]:
        """{kalshi_id: poly_id} for human-verified pairs."""
        conn = sqlite3.connect(self.path)
        rows = conn.execute("SELECT kalshi_id, polymarket_id FROM matches "
                            "WHERE verified=1 AND rejected=0").fetchall()
        conn.close()
        return dict(rows)

    def get_rejected(self) -> set[tuple[str, str]]:
        conn = sqlite3.connect(self.path)
        rows = conn.execute("SELECT kalshi_id, polymarket_id FROM matches WHERE rejected=1").fetchall()
        conn.close()
        return set(rows)

    def upsert(self, kalshi_id: str, poly_id: str, confidence: float,
               method: str, verified: bool = False, rejected: bool = False,
               notes: str = ""):
        conn = sqlite3.connect(self.path)
        conn.execute("""INSERT OR REPLACE INTO matches
            (kalshi_id, polymarket_id, confidence, method, verified, rejected, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (kalshi_id, poly_id, confidence, method, int(verified), int(rejected), notes))
        conn.commit(); conn.close()

    def mark_verified(self, kalshi_id: str, poly_id: str, notes: str = ""):
        self.upsert(kalshi_id, poly_id, 1.0, "manual", verified=True, notes=notes)

    def mark_rejected(self, kalshi_id: str, poly_id: str, notes: str = ""):
        self.upsert(kalshi_id, poly_id, 0.0, "manual", rejected=True, notes=notes)


class MarketMatcher:
    def __init__(self,
                 cache_path: str = "data/cache/matches_cache.db",
                 min_score: float = 0.60,
                 auto_match_threshold: float = 0.90,
                 max_resolution_delta_hours: float = 24.0,
                 use_semantic: bool = False):
        self.cache = MatchCache(cache_path)
        self.min_score = min_score
        self.auto_threshold = auto_match_threshold
        self.max_delta = timedelta(hours=max_resolution_delta_hours)
        self.use_semantic = use_semantic
        self._semantic_model = None  # lazy load
        log.info(f"MarketMatcher init  min_score={min_score} auto_threshold={auto_match_threshold} semantic={use_semantic}")

    def _semantic_score(self, a: str, b: str) -> float:
        if self._semantic_model is None:
            try:
                from sentence_transformers import SentenceTransformer, util
                self._semantic_model = SentenceTransformer("all-MiniLM-L6-v2")
                self._util = util
            except ImportError:
                log.warning("sentence-transformers not installed; disabling semantic matching")
                self.use_semantic = False
                return 0.0
        emb = self._semantic_model.encode([a, b], convert_to_tensor=True)
        return float(self._util.cos_sim(emb[0], emb[1]).item())

    def _title_score(self, a: str, b: str) -> float:
        """Combined fuzzy score 0..1."""
        s1 = fuzz.token_set_ratio(a, b) / 100.0
        s2 = fuzz.partial_ratio(a, b) / 100.0
        return max(s1, s2 * 0.85)  # token_set is more robust; weight partial slightly less

    def _date_compatible(self, a: NormalizedMarket, b: NormalizedMarket) -> tuple[bool, float]:
        """Are the resolution dates within tolerance? Returns (ok, delta_hours)."""
        if not a.resolution_date or not b.resolution_date:
            return True, 0.0  # benefit of doubt — manual review will catch
        delta = abs(a.resolution_date - b.resolution_date)
        return delta <= self.max_delta, delta.total_seconds() / 3600

    def match(self,
              kalshi_markets: list[NormalizedMarket],
              poly_markets: list[NormalizedMarket],
              strike_tolerance_pct: float = 0.001) -> list[MatchedMarketPair]:
        """Return candidate matches.

        Strike-aware logic:
          1. For each (event_family, resolution_date), pair Kalshi strikes with
             Polymarket strikes at the SAME numeric strike (±0.1% tolerance).
          2. Strike-paired matches get confidence based on family agreement +
             strike-exactness (1.0 for exact match, falling off with tolerance).
          3. For markets without extractable strikes (politics, sports, etc.),
             fall back to fuzzy title matching as before.
        """
        verified = self.cache.get_verified()
        rejected = self.cache.get_rejected()
        for k_id, p_match in MANUAL_PAIRS.items():
            if k_id not in verified: verified[k_id] = p_match

        poly_by_id = {p.market_id: p for p in poly_markets}
        poly_by_slug = {(p.raw.get("slug") or ""): p for p in poly_markets if p.raw}

        pairs: list[MatchedMarketPair] = []
        used_pairs = set()  # set of (k_id, p_id)

        # ── 1. Verified pairs from cache ─────────────────────────────
        for k in kalshi_markets:
            if k.market_id not in verified: continue
            poly_key = verified[k.market_id]
            p = poly_by_id.get(poly_key) or poly_by_slug.get(poly_key)
            if not p: continue
            ok, delta_h = self._date_compatible(k, p)
            pairs.append(MatchedMarketPair(
                kalshi_market=k, polymarket_market=p,
                confidence=1.0, method=MatchMethod.MANUAL,
                resolution_date_delta_hours=delta_h, verified_by_human=True))
            used_pairs.add((k.market_id, p.market_id))

        # ── 2. Strike-aware matching ─────────────────────────────────
        # Group by (event_family) and pair within each group by strike value
        k_by_family: dict[str, list[NormalizedMarket]] = {}
        p_by_family: dict[str, list[NormalizedMarket]] = {}
        for k in kalshi_markets:
            if k.market_id in verified: continue
            if kalshi_strike(k) is None: continue
            k_by_family.setdefault(underlying_key(k), []).append(k)
        for p in poly_markets:
            if polymarket_strike(p) is None: continue
            p_by_family.setdefault(underlying_key(p), []).append(p)

        for family, ks in k_by_family.items():
            ps = p_by_family.get(family, [])
            if not ps: continue
            for k in ks:
                k_strike = kalshi_strike(k)
                # Find the closest Polymarket strike within tolerance
                best_p = None; best_delta = float("inf")
                for p in ps:
                    p_strike = polymarket_strike(p)
                    if p_strike is None: continue
                    rel = abs(k_strike - p_strike) / max(k_strike, p_strike)
                    if rel < best_delta:
                        best_p = p; best_delta = rel
                if best_p is None: continue
                if best_delta > strike_tolerance_pct: continue
                if (k.market_id, best_p.market_id) in rejected: continue
                ok, delta_h = self._date_compatible(k, best_p)
                if not ok: continue
                # Confidence: exact strike match = 1.0, decays toward tolerance edge
                conf = 1.0 - (best_delta / strike_tolerance_pct) * 0.1
                pairs.append(MatchedMarketPair(
                    kalshi_market=k, polymarket_market=best_p,
                    confidence=conf, method=MatchMethod.EXACT,
                    resolution_date_delta_hours=delta_h,
                    verified_by_human=False,
                    notes=f"strike-pair: k_strike={k_strike:.0f} p_strike={polymarket_strike(best_p):.0f}"))
                used_pairs.add((k.market_id, best_p.market_id))

        # ── 3. Fuzzy title matching for non-strike markets ───────────
        nonstrike_k = [k for k in kalshi_markets
                       if k.market_id not in verified
                       and not any(k.market_id == kid for (kid, _) in used_pairs)
                       and kalshi_strike(k) is None]
        used_p_ids = {pid for (_, pid) in used_pairs}
        nonstrike_p = [p for p in poly_markets
                       if p.market_id not in used_p_ids
                       and polymarket_strike(p) is None]
        if nonstrike_k and nonstrike_p:
            poly_titles = [p.title for p in nonstrike_p]
            for k in nonstrike_k:
                results = process.extract(k.title, poly_titles, scorer=fuzz.token_set_ratio, limit=3)
                for poly_title, score, idx in results:
                    score_norm = score / 100.0
                    if score_norm < self.min_score: continue
                    p = nonstrike_p[idx]
                    if (k.market_id, p.market_id) in rejected: continue
                    ok, delta_h = self._date_compatible(k, p)
                    if not ok: continue
                    final_score = score_norm
                    if self.use_semantic and score_norm < self.auto_threshold:
                        sem = self._semantic_score(k.title, p.title)
                        final_score = max(score_norm, sem)
                    method = MatchMethod.SEMANTIC if (self.use_semantic and final_score > score_norm) \
                             else (MatchMethod.EXACT if score_norm >= 0.95 else MatchMethod.FUZZY)
                    pairs.append(MatchedMarketPair(
                        kalshi_market=k, polymarket_market=p,
                        confidence=final_score, method=method,
                        resolution_date_delta_hours=delta_h,
                        verified_by_human=False))
                    used_pairs.add((k.market_id, p.market_id))
                    break  # take best per Kalshi market

        # Sort by confidence desc; deduplication is per-PAIR now (not per market)
        pairs.sort(key=lambda x: -x.confidence)
        seen, out = set(), []
        for pp in pairs:
            key = (pp.kalshi_market.market_id, pp.polymarket_market.market_id)
            if key in seen: continue
            seen.add(key); out.append(pp)
        n_strike = sum(1 for pp in out if kalshi_strike(pp.kalshi_market) is not None)
        log.info(f"matched {len(out)} pairs  ({n_strike} strike-aligned, "
                 f"{len(out)-n_strike} fuzzy, "
                 f"{sum(1 for p in out if p.verified_by_human)} verified)")
        return out
