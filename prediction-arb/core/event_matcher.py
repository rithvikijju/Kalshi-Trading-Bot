"""Event-level cross-platform matcher.

Strategy:
  1. Match Kalshi EVENTS to Polymarket EVENTS using:
     - Manual curated mapping (high-confidence, hand-verified pairs)
     - Title fuzzy match + category/tags overlap + date proximity
  2. Within each matched event, pair child MARKETS by:
     - Outcome name match (Kalshi.yes_sub_title ↔ Poly.groupItemTitle)
     - OR numeric strike match (Kalshi.floor_strike ↔ Poly.groupItemThreshold)

This is far more accurate than per-market title fuzzing because it uses
the structural metadata both platforms expose.
"""
from __future__ import annotations
import re, sys
from pathlib import Path
from datetime import timedelta
from typing import Optional

from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.models import (NormalizedEvent, NormalizedMarket, MatchedEventPair,
                          MatchedMarketPair, MatchMethod, Platform)
from utils.logger import setup_logger

log = setup_logger("event_matcher")


# ─── Manual curation table (extend as you verify pairs) ─────────────
# Kalshi event_ticker → Polymarket event slug
MANUAL_EVENT_PAIRS: dict[str, str] = {
    # Fill in as you verify pairs in notebook 02:
    # "KXFEDDECISION-26JUN": "fed-decision-june-2026",
    # "KXPRES-2028":          "republican-presidential-nominee-2028",
    # "KXWORLDCUP-26":        "2026-fifa-world-cup-winner-595",
}


# Series prefix → likely category mapping (used as a soft prior)
SERIES_CATEGORY: dict[str, str] = {
    "KXFEDDECISION": "economics", "KXCPI": "economics", "KXJOBS": "economics",
    "KXGDP": "economics", "KXPCE": "economics", "KXNFP": "economics",
    "KXRECESSION": "economics", "KXRATE": "economics",
    "KXPRES": "politics", "KXSEN": "politics", "KXHOUSE": "politics",
    "KXGOV": "politics", "KXIMPEACH": "politics", "KXPRIMARY": "politics",
    "KXBTC": "crypto", "KXBTCD": "crypto", "KXETH": "crypto",
    "KXETHD": "crypto", "KXSOL": "crypto",
    "KXSPX": "finance", "KXNASDAQ": "finance", "KXSPY": "finance",
    "KXNBA": "sports", "KXNFL": "sports", "KXMLB": "sports",
    "KXWORLDCUP": "sports", "KXSOC": "sports", "KXOLY": "sports",
    "KXWAR": "geopolitics", "KXPUTIN": "geopolitics",
    "KXOSCAR": "culture", "KXGRAMMY": "culture",
}


# ─── Normalization helpers ──────────────────────────────────────────
_PUNCT_RE = re.compile(r'[^\w\s]')
# NOTE: normalize bps spellings, but DO NOT collapse cut/hike → direction is
# load-bearing. The direction-check function below handles cut↔decrease.
_BPS_SYNONYM = [
    (r'\b25bps?\b',  '25 basis points'),
    (r'\b50bps?\b',  '50 basis points'),
    (r'\b100bps?\b', '100 basis points'),
    (r'\bbps?\b',    'basis points'),
]


# Asset detection — required to match across crypto events
_ASSET_PATTERNS = [
    ('btc',  re.compile(r'\b(bitcoin|btc)\b', re.IGNORECASE)),
    ('eth',  re.compile(r'\b(ethereum|eth)\b', re.IGNORECASE)),
    ('sol',  re.compile(r'\b(solana|sol)\b', re.IGNORECASE)),
    ('xrp',  re.compile(r'\bxrp\b', re.IGNORECASE)),
    ('doge', re.compile(r'\b(dogecoin|doge)\b', re.IGNORECASE)),
]


def detect_asset(text: str) -> Optional[str]:
    if not text: return None
    # Check more specific names first (bitcoin before btc, etc.)
    for asset, pat in _ASSET_PATTERNS:
        if pat.search(text): return asset
    return None


# Market-type detection — buckets are structurally different from thresholds.
# A YES on Kalshi "$X to Y" (bucket) pays only if price lands in a narrow band,
# while a YES on Polymarket "X or above" (threshold) pays for any price above X.
# Pairing them looks like arb (strike numbers align) but isn't (events differ).
def market_type(s: str, floor=None, cap=None) -> str:
    """Returns 'bucket' | 'threshold_up' | 'threshold_down' | 'unknown'."""
    if floor is not None and cap is not None:
        try:
            f, c = float(floor), float(cap)
            # If cap is a finite small range from floor, it's a bucket
            if c - f < f * 0.01:   # cap within 1% of floor → narrow bucket
                return 'bucket'
        except (TypeError, ValueError):
            pass
    if not s: return 'unknown'
    t = s.lower()
    # Bucket: "$X to $Y", "X-Y", "between X and Y"
    if any(p in t for p in (' to ', '-to-', 'between')) and any(c.isdigit() for c in t):
        # could be a date "May 18 to 20"; check there are 2 numbers separated by " to "
        nums = re.findall(r'\d[\d,]*\.?\d*', t)
        if len(nums) >= 2:
            return 'bucket'
    # Threshold up
    if any(w in t for w in ('or above', '↑', 'above', 'higher', 'over', 'reach', 'hit')):
        return 'threshold_up'
    # Threshold down
    if any(w in t for w in ('or below', '↓', 'below', 'lower', 'under')):
        return 'threshold_down'
    return 'unknown'


def market_types_compatible(t1: str, t2: str) -> bool:
    """Bucket only pairs with bucket. thresholds compatible with thresholds
    (regardless of direction — direction is handled by outcome_direction)."""
    if t1 == 'unknown' or t2 == 'unknown':
        return True   # benefit of doubt for ambiguous strings
    if t1 == 'bucket' or t2 == 'bucket':
        return t1 == t2 == 'bucket'
    return True   # both thresholds → compatible


# Direction detection — markets at the same strike but opposite directions
# are different events. Direction must align (or both be 'unknown').
def outcome_direction(s: str) -> str:
    """Returns 'up' | 'down' | 'neutral' | 'unknown'."""
    if not s: return 'unknown'
    t = s.lower()
    # neutral (Fed hold, no change)
    if any(w in t for w in ('hold ', 'holds', 'maintain', 'unchanged', 'no change',
                             'no rate change', 'no decision')):
        return 'neutral'
    # up
    if any(w in t for w in ('hike', 'increase', 'above', 'higher', 'rise', 'raise',
                             '↑', 'over ', 'or above', 'reach $', 'hit $')):
        return 'up'
    # down
    if any(w in t for w in ('cut', 'decrease', 'below', 'lower', 'dip', 'reduce',
                             'fall', '↓', 'under ', 'or below')):
        return 'down'
    return 'unknown'


def normalize_text(s: str) -> str:
    """Lowercase, strip punctuation, expand Fed-style abbreviations."""
    if not s: return ""
    t = s.lower().strip()
    for pat, repl in _BPS_SYNONYM:
        t = re.sub(pat, repl, t)
    t = _PUNCT_RE.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def extract_number(s: str) -> Optional[float]:
    """Pull the first numeric value out of a string. Handles commas + k/m suffixes."""
    if not s: return None
    m = re.search(r'(\d[\d,]*\.?\d*)\s*(k|K|m|M|thousand|million)?', s)
    if not m: return None
    raw = m.group(1).replace(',', '')
    try: v = float(raw)
    except ValueError: return None
    suffix = (m.group(2) or '').lower()
    if suffix in ('k', 'thousand'): v *= 1000
    elif suffix in ('m', 'million'): v *= 1_000_000
    return v


# ─── Date-aware event keying ────────────────────────────────────────
_MONTH_MAP = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
    'jul':7,'aug':8,'sep':9,'sept':9,'oct':10,'nov':11,'dec':12,
    'january':1,'february':2,'march':3,'april':4,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
}


def parse_event_date_hints(event: NormalizedEvent) -> set[str]:
    """Extract MONTH-level date hints from title + sub_title + end_date."""
    out = set()
    if event.end_date:
        out.add(event.end_date.strftime("%Y-%m"))
    text = f"{event.title} {event.sub_title}".lower()
    for m in re.finditer(r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
                          r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
                          r'nov(?:ember)?|dec(?:ember)?)\s+(\d{4})', text):
        month_name = m.group(1)
        year = m.group(2)
        month_num = _MONTH_MAP.get(month_name[:3].lower())
        if month_num:
            out.add(f"{year}-{month_num:02d}")
    for m in re.finditer(r'\b(\d{4})-(\d{1,2})\b', text):
        out.add(f"{m.group(1)}-{int(m.group(2)):02d}")
    return out


def parse_specific_days(event: NormalizedEvent) -> set[str]:
    """Extract DAY-level dates from title text (e.g. 'May 18', 'June 26, 2026').
    Returns set of MM-DD strings. Used as a HARD GATE for daily-resolution events."""
    out = set()
    text = f"{event.title} {event.sub_title}".lower()
    # "May 18", "May 18, 2026", "June 26, 2026"
    for m in re.finditer(
        r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
        r'jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|'
        r'nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:[,\s]+\d{4})?\b', text):
        month_name = m.group(1)
        day = m.group(2)
        month_num = _MONTH_MAP.get(month_name[:3].lower())
        if month_num and 1 <= int(day) <= 31:
            out.add(f"{month_num:02d}-{int(day):02d}")
    return out


# Stop words that appear in many unrelated markets and don't carry meaning
_STOP_SUBJECTS = {
    # Common political subjects
    "trump", "biden", "harris", "vance", "newsom", "kamala", "donald", "joe",
    "president", "presidential",
    # Crypto
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "price",
    # Fed/macro
    "fed", "federal", "reserve", "interest", "rate", "rates", "fomc",
    # Generic
    "will", "the", "be", "is", "in", "on", "at", "of", "by", "for", "with",
    "a", "an", "to", "and", "or", "as", "this", "that", "year", "month", "day",
    "us", "u.s.", "above", "below",
}


def predicate_similarity(t1: str, t2: str) -> float:
    """Title similarity AFTER stripping shared-subject stop-words.

    Two markets that both say 'Will Trump...' shouldn't score high just on the
    'Trump' overlap. This computes similarity on the remaining content words.
    Returns 0..1.
    """
    n1 = normalize_text(t1); n2 = normalize_text(t2)
    if not n1 or not n2: return 0.0
    words1 = [w for w in n1.split() if w not in _STOP_SUBJECTS and len(w) > 1]
    words2 = [w for w in n2.split() if w not in _STOP_SUBJECTS and len(w) > 1]
    # If after stop-word removal one side is empty, the original similarity
    # was probably ALL from stop words — give a very low predicate score.
    if not words1 or not words2: return 0.10
    p1 = " ".join(words1); p2 = " ".join(words2)
    # Use multiple scorers and take the average — more robust than any single
    s_set     = fuzz.token_set_ratio(p1, p2) / 100.0
    s_sort    = fuzz.token_sort_ratio(p1, p2) / 100.0
    s_partial = fuzz.partial_ratio(p1, p2) / 100.0
    # token_set is most lenient (bag-of-words). Weight sort + partial higher
    # to demand more structural similarity.
    return 0.20 * s_set + 0.40 * s_sort + 0.40 * s_partial


# ─── EventMatcher ───────────────────────────────────────────────────
class EventMatcher:
    def __init__(self,
                 min_event_score: float = 0.65,
                 strike_tolerance_pct: float = 0.02,
                 max_end_date_delta_days: int = 14,
                 min_predicate_score: float = 0.55,
                 require_min_market_pairs: int = 1):
        self.min_event_score = min_event_score
        self.strike_tolerance_pct = strike_tolerance_pct
        self.max_end_date_delta = timedelta(days=max_end_date_delta_days)
        self.min_predicate_score = min_predicate_score
        self.require_min_market_pairs = require_min_market_pairs

    def match(self,
              kalshi_events: list[NormalizedEvent],
              poly_events: list[NormalizedEvent]) -> list[MatchedEventPair]:
        """Top-level: pair events, then pair child markets within each.
        Drop event pairs that produced fewer than `require_min_market_pairs`."""
        candidates = self._match_events(kalshi_events, poly_events)
        kept: list[MatchedEventPair] = []
        for ep in candidates:
            ep.market_pairs = self._match_markets_within(ep)
            if len(ep.market_pairs) >= self.require_min_market_pairs or ep.verified_by_human:
                kept.append(ep)
        log.info(f"event_pairs={len(kept)} (dropped {len(candidates)-len(kept)} with 0 child pairs)  "
                 f"total_market_pairs={sum(len(ep.market_pairs) for ep in kept)}")
        return kept

    def _match_events(self, k_events: list[NormalizedEvent],
                      p_events: list[NormalizedEvent]) -> list[MatchedEventPair]:
        """Pair events. Each Kalshi event can match ONE Polymarket event (best match)."""
        # Build a Poly index keyed by slug for fast manual lookup
        poly_by_slug = {pe.event_id: pe for pe in p_events}
        used_poly = set()
        pairs: list[MatchedEventPair] = []

        # 1. Manual curated pairs first
        for k in k_events:
            if k.event_id in MANUAL_EVENT_PAIRS:
                slug = MANUAL_EVENT_PAIRS[k.event_id]
                p = poly_by_slug.get(slug)
                if p:
                    pairs.append(MatchedEventPair(
                        kalshi_event=k, polymarket_event=p,
                        confidence=1.0, method=MatchMethod.MANUAL,
                        verified_by_human=True,
                    ))
                    used_poly.add(p.event_id)

        # 2. Fuzzy + metadata score
        for k in k_events:
            if any(ep.kalshi_event.event_id == k.event_id for ep in pairs):
                continue
            best = None; best_score = 0.0; best_delta = None
            k_months = parse_event_date_hints(k)
            k_days   = parse_specific_days(k)
            k_cat = SERIES_CATEGORY.get(k.series.upper(), k.category)
            for p in p_events:
                if p.event_id in used_poly: continue

                # Gate 1: must share category OR strong topic keyword
                cat_overlap = (k_cat and k_cat == p.category)
                tag_text = " ".join(p.tags).lower() if p.tags else ""
                kw_overlap = any(kw in tag_text for kw in self._series_keywords(k.series))
                if not (cat_overlap or kw_overlap):
                    continue

                # Gate 1b: ASSET MUST MATCH (BTC/ETH/SOL etc.) — prevents
                # BTC event being paired with an ETH event just because they
                # share "crypto" tags and the same date.
                k_asset = detect_asset(f"{k.title} {k.series}")
                p_asset = detect_asset(f"{p.title} {tag_text}")
                if k_asset and p_asset and k_asset != p_asset:
                    continue

                # Gate 2: STRICT DAY-LEVEL DATE MATCH (when both sides have a specific day)
                p_days = parse_specific_days(p)
                if k_days and p_days:
                    if not (k_days & p_days):
                        continue   # different specific days → not the same event
                # Otherwise allow month-level overlap or no date

                # Gate 3: predicate similarity (after stripping shared subjects)
                pred_score = predicate_similarity(k.title, p.title)
                if pred_score < self.min_predicate_score:
                    continue

                # Composite score
                # Base = predicate similarity. Bonus = month overlap, end-date proximity.
                p_months = parse_event_date_hints(p)
                month_overlap = bool(k_months & p_months)
                month_bonus = 0.10 if month_overlap else 0.0
                end_delta_days = None
                if k.end_date and p.end_date:
                    end_delta_days = abs((k.end_date - p.end_date).total_seconds()) / 86400
                end_bonus = 0.0
                if end_delta_days is not None:
                    if end_delta_days <= self.max_end_date_delta.days:
                        end_bonus = 0.10 * max(0, 1 - end_delta_days / self.max_end_date_delta.days)
                    else:
                        end_bonus = -0.20

                score = pred_score + month_bonus + end_bonus

                if score > best_score and score >= self.min_event_score:
                    best = p; best_score = score; best_delta = end_delta_days or 0.0
            if best is not None:
                pairs.append(MatchedEventPair(
                    kalshi_event=k, polymarket_event=best,
                    confidence=min(best_score, 1.0),
                    method=MatchMethod.FUZZY,
                    end_date_delta_hours=(best_delta or 0) * 24,
                ))
                used_poly.add(best.event_id)
        return pairs

    def _series_keywords(self, series: str) -> list[str]:
        """Keywords that should appear in a Polymarket tag if it's the same family."""
        s = series.upper()
        kw_map = {
            "KXFEDDECISION": ["fed", "federal reserve", "interest rate", "fomc", "rate"],
            "KXCPI": ["cpi", "inflation"],
            "KXJOBS": ["jobs", "nonfarm", "unemployment", "nfp"],
            "KXGDP": ["gdp"],
            "KXPRES": ["president", "election", "trump", "biden", "harris", "vance", "newsom"],
            "KXSEN": ["senate", "senator"],
            "KXHOUSE": ["house", "congress"],
            "KXBTC": ["bitcoin", "btc"], "KXBTCD": ["bitcoin", "btc"],
            "KXETH": ["ethereum", "eth"], "KXETHD": ["ethereum", "eth"],
            "KXSOL": ["solana", "sol"],
            "KXSPX": ["s&p", "sp500"], "KXNASDAQ": ["nasdaq"],
            "KXWORLDCUP": ["world cup", "fifa", "soccer"],
            "KXNBA": ["nba", "basketball"],
            "KXNFL": ["nfl", "football"], "KXMLB": ["mlb", "baseball"],
            "KXWAR": ["war", "ukraine", "russia", "israel", "iran"],
            "KXPUTIN": ["putin", "russia"],
            "KXOSCAR": ["oscar", "academy"],
        }
        return kw_map.get(s, [s.lower().replace("kx", "")])

    def _match_markets_within(self, ep: MatchedEventPair) -> list[MatchedMarketPair]:
        """Within a matched event, pair child markets by outcome label or strike."""
        k_markets = ep.kalshi_event.markets
        p_markets = ep.polymarket_event.markets
        if not k_markets or not p_markets: return []

        used_p = set()
        out: list[MatchedMarketPair] = []
        for k in k_markets:
            # Build a canonical "outcome key" for the Kalshi market
            k_yes_sub = (k.raw.get("yes_sub_title") or "").strip()
            k_floor = k.raw.get("floor_strike") or k.raw.get("cap_strike")
            k_cap_raw = k.raw.get("cap_strike")
            k_floor_raw = k.raw.get("floor_strike")
            k_outcome_n = normalize_text(k_yes_sub) or normalize_text(k.title)
            k_num = extract_number(k_yes_sub) or (float(k_floor) if k_floor else None) \
                    or extract_number(k.market_id)
            k_dir = outcome_direction(k_yes_sub) if k_yes_sub else 'unknown'
            # Classify Kalshi market type. Buckets have BOTH floor+cap set with
            # finite range; thresholds have only one bound (other side is open).
            k_type = market_type(k_yes_sub, floor=k_floor_raw, cap=k_cap_raw)
            # Kalshi ticker pattern -B<num> = bucket, -T<num> = threshold above
            mid = (k.market_id or "").upper()
            if '-B' in mid and k_type == 'unknown': k_type = 'bucket'
            elif '-T' in mid and k_type == 'unknown': k_type = 'threshold_up'

            best_p = None; best_score = 0.0; best_method = MatchMethod.EVENT_OUTCOME
            for p in p_markets:
                if p.market_id in used_p: continue
                p_git = (p.raw.get("groupItemTitle") or "").strip()
                p_threshold = p.raw.get("groupItemThreshold")
                p_outcome_n = normalize_text(p_git) or normalize_text(p.title)
                p_dir = outcome_direction(p_git) if p_git else 'unknown'
                p_type = market_type(p_git)

                # HARD GATE: direction must align (or both unknown).
                # Prevents pairing Kalshi "Cut 25bps" (down) with Poly "25 bps
                # increase" (up) just because the numeric strike matches.
                if k_dir != 'unknown' and p_dir != 'unknown' and k_dir != p_dir:
                    continue

                # HARD GATE: market types must be compatible. A Kalshi bucket
                # ($72,600 to $72,699.99) is NOT the same as a Polymarket
                # threshold (BTC > $72,650). Both legs can lose simultaneously
                # — the "arb" math is bogus. Reject bucket↔threshold pairs.
                if not market_types_compatible(k_type, p_type):
                    continue

                # 1. Outcome name fuzzy
                name_score = 0.0
                if k_outcome_n and p_outcome_n:
                    name_score = fuzz.token_set_ratio(k_outcome_n, p_outcome_n) / 100.0

                # 2. Numeric strike alignment (if both sides have a number)
                num_score = 0.0
                p_num = extract_number(p_git) or (float(p_threshold) if p_threshold not in (None, "0") else None) \
                        or extract_number(p.title)
                if k_num is not None and p_num is not None and k_num > 0 and p_num > 0:
                    rel_err = abs(k_num - p_num) / max(k_num, p_num)
                    if rel_err <= self.strike_tolerance_pct:
                        num_score = 1.0 - (rel_err / self.strike_tolerance_pct) * 0.1
                        # When numbers match cleanly, that's a strong signal
                        best_method_cand = MatchMethod.EVENT_STRIKE
                    else:
                        num_score = 0.0
                        best_method_cand = MatchMethod.EVENT_OUTCOME
                else:
                    best_method_cand = MatchMethod.EVENT_OUTCOME

                combined = max(name_score, num_score)
                if combined > best_score:
                    best_p = p; best_score = combined
                    best_method = best_method_cand

            if best_p is not None and best_score >= 0.55:
                used_p.add(best_p.market_id)
                out.append(MatchedMarketPair(
                    kalshi_market=k,
                    polymarket_market=best_p,
                    confidence=best_score * (ep.confidence ** 0.5),  # weight by event confidence
                    method=best_method,
                    resolution_date_delta_hours=ep.end_date_delta_hours,
                    notes=f"in event: {ep.kalshi_event.event_id} ↔ {ep.polymarket_event.event_id}",
                ))
        return out


def flatten_market_pairs(event_pairs: list[MatchedEventPair]) -> list[MatchedMarketPair]:
    """Helper: flatten event-pair tree into a flat list of market pairs."""
    out: list[MatchedMarketPair] = []
    for ep in event_pairs:
        out.extend(ep.market_pairs)
    out.sort(key=lambda mp: -mp.confidence)
    return out
