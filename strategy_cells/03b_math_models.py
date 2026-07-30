# § 2b — Mathematical models (classical quant, no ML)
#
# Stacks on top of §2's data pipeline. Provides:
#   - EWMA volatility estimator (RiskMetrics 1996) — adapts faster than rolling
#   - Welford running stats (Welford 1962) — numerically stable mean/variance
#   - Hampel filter (median + MAD) for spot outlier rejection
#   - Black-Scholes binary with drift correction
#   - Merton jump-diffusion overlay (handles fat tails)
#   - Kelly fraction position sizing
#   - SPRT (Wald 1947) for per-tier auto-disable on declining win rate
#
# All deterministic, no hidden state across runs (except SPRT which persists
# its log-likelihood ratio to the trades DB).


# ─────────────────────────────────────────────────────────────────
# 1. EWMA volatility estimator (RiskMetrics)
#    σ²_t = λ × σ²_{t-1} + (1−λ) × r²_t
#    λ = 0.94 is the standard RiskMetrics value (~ 75-period half-life)
# ─────────────────────────────────────────────────────────────────

EWMA_STATE = {'sigma_sq': None, 'last_price': None, 'lambda': 0.94, 'n_updates': 0}


def ewma_update(new_price: float):
    """Update EWMA σ estimate with a new spot price."""
    if EWMA_STATE['last_price'] is None:
        EWMA_STATE['last_price'] = new_price
        return
    r = math.log(new_price / EWMA_STATE['last_price'])
    EWMA_STATE['last_price'] = new_price
    EWMA_STATE['n_updates'] += 1
    if EWMA_STATE['sigma_sq'] is None:
        EWMA_STATE['sigma_sq'] = r * r
    else:
        l = EWMA_STATE['lambda']
        EWMA_STATE['sigma_sq'] = l * EWMA_STATE['sigma_sq'] + (1 - l) * r * r


def ewma_sigma_annual() -> Optional[float]:
    """Return annualized σ from EWMA estimator. None if not enough data."""
    if EWMA_STATE['sigma_sq'] is None or EWMA_STATE['n_updates'] < 30:
        return None
    # σ is per-tick. Spot is polled every CFG['spot_poll_sec'] seconds.
    sigma_per_tick = math.sqrt(EWMA_STATE['sigma_sq'])
    ticks_per_year = (365.25 * 24 * 3600) / CFG['spot_poll_sec']
    return sigma_per_tick * math.sqrt(ticks_per_year)


# ─────────────────────────────────────────────────────────────────
# 2. Hampel filter for spot outlier rejection
#    Reject any spot tick that is > k × MAD away from the rolling median
#    Default k=3 (Hampel's identifier; mild outliers)
# ─────────────────────────────────────────────────────────────────

def _median(xs):
    s = sorted(xs); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _mad(xs, med):
    return _median([abs(x - med) for x in xs])


def spot_is_outlier(new_price: float, window: int = 30, k: float = 3.0,
                    min_abs_threshold: float = 50.0,
                    min_pct_threshold: float = 0.005,
                    max_stale_sec: float = 60.0) -> bool:
    """Hampel filter — True if new_price is an anomalous spike.

    Three guards prevent over-rejection:
      1. Absolute floor ($50) — for normal BTC ticks during quiet periods.
      2. Percent floor (0.5%) — at $80K spot, threshold ≥ $400 so a trending
         move ($300 in one tick) doesn't look like an outlier.
      3. Watchdog (60s) — if we haven't accepted a tick in too long, force-
         accept. Prevents the "median anchored to stale price" failure mode
         where the filter rejects everything forever after spot moves more
         than the threshold while the bot was running.
    """
    # Watchdog: if our last-accepted tick is stale, bypass the filter entirely.
    # The median is anchored to old data and can't recover until we let
    # something through.
    with _LOCK:
        last_ts = SPOT.get('ts')
    if last_ts is not None:
        age = (datetime.now(timezone.utc) - last_ts).total_seconds()
        if age > max_stale_sec:
            return False  # force-accept, will rebuild history from here
    with _LOCK:
        hist = [p for _, p in list(SPOT['history'])[-window:]]
    if len(hist) < 10:
        return False
    med = _median(hist)
    mad = _mad(hist, med)
    # MAD-to-σ scale: ~1.4826
    threshold = max(min_abs_threshold,
                    min_pct_threshold * med,
                    k * 1.4826 * mad)
    return abs(new_price - med) > threshold


# ─────────────────────────────────────────────────────────────────
# 3. Welford running stats — used by SPRT for win-rate
# ─────────────────────────────────────────────────────────────────

class Welford:
    """Online mean + variance, numerically stable."""
    __slots__ = ('n', 'mean', 'M2')
    def __init__(self):
        self.n = 0; self.mean = 0.0; self.M2 = 0.0
    def update(self, x):
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.M2 += delta * delta2
    def variance(self):
        return self.M2 / max(1, self.n - 1)
    def stddev(self):
        return math.sqrt(self.variance())


# ─────────────────────────────────────────────────────────────────
# 4. Black-Scholes binary with drift correction
#    Standard: d = (spot − K) / σ√t  (assumes zero drift)
#    With drift: d = (ln(spot/K) + μ × t) / (σ × √t × spot)
#
#    We measure μ from recent log-returns (annualized).
#    For 30-60min windows, BTC's daily drift is essentially noise so
#    we cap |μ| at ±0.5 annual (±50% / year) to prevent extreme estimates.
# ─────────────────────────────────────────────────────────────────

DRIFT_STATE = {'log_returns': [], 'last_price': None, 'window': 600}


def drift_update(new_price: float):
    if DRIFT_STATE['last_price'] is not None:
        r = math.log(new_price / DRIFT_STATE['last_price'])
        DRIFT_STATE['log_returns'].append(r)
        if len(DRIFT_STATE['log_returns']) > DRIFT_STATE['window']:
            DRIFT_STATE['log_returns'].pop(0)
    DRIFT_STATE['last_price'] = new_price


def estimated_drift_annual() -> float:
    """Return capped annualized drift. 0.0 if not enough data."""
    rs = DRIFT_STATE['log_returns']
    if len(rs) < 60:
        return 0.0
    mean_per_tick = sum(rs) / len(rs)
    ticks_per_year = (365.25 * 24 * 3600) / CFG['spot_poll_sec']
    drift = mean_per_tick * ticks_per_year
    return max(-0.5, min(0.5, drift))  # cap ±50%/yr


def fair_yes_with_drift(spot: float, strike: float, secs_to_close: float,
                       sigma_annual: float, mu_annual: float = 0.0) -> float:
    """Black-Scholes binary with drift. Returns P(spot_T > K)."""
    t = secs_to_close / (365.25 * 24 * 3600)
    if t <= 0 or sigma_annual <= 0 or spot <= 0:
        return 1.0 if spot > strike else 0.0
    # Risk-neutral N(d2) with drift μ
    sigma_sq = sigma_annual * sigma_annual
    d = (math.log(spot / strike) + (mu_annual - 0.5 * sigma_sq) * t) / (sigma_annual * math.sqrt(t))
    return _norm_cdf(d)


# ─────────────────────────────────────────────────────────────────
# 5. Merton jump-diffusion overlay for fat-tail fair value
#    fair_jump = Σ_{k=0}^{N} e^{-λt} × (λt)^k / k! × fair_BS(σ_k)
#    where σ_k incorporates k jumps. We approximate with N=5.
#    Conservative: increases P(extreme moves), making fair_yes lower
#    near strikes (i.e., MORE skeptical of deep-ITM contracts).
# ─────────────────────────────────────────────────────────────────

def fair_yes_jump(spot: float, strike: float, secs_to_close: float,
                  sigma_annual: float, mu_annual: float = 0.0,
                  jump_rate_per_year: float = 12.0,
                  jump_size_pct: float = 0.02) -> float:
    """Jump-diffusion fair value. jump_rate=12 means ~12 jumps/yr expected.
    Approximates BTC's ~monthly significant move pattern."""
    t = secs_to_close / (365.25 * 24 * 3600)
    if t <= 0 or sigma_annual <= 0:
        return 1.0 if spot > strike else 0.0

    lambda_t = jump_rate_per_year * t
    # Poisson probabilities for 0..5 jumps
    fair = 0.0
    fact = 1.0
    p_total = 0.0
    for k in range(6):
        if k > 0: fact *= k
        p_k = math.exp(-lambda_t) * (lambda_t ** k) / fact
        p_total += p_k
        # Effective σ inflated by k jumps
        sigma_k_sq = sigma_annual * sigma_annual + k * (jump_size_pct ** 2) / t
        sigma_k = math.sqrt(max(0.0001, sigma_k_sq))
        fair += p_k * fair_yes_with_drift(spot, strike, secs_to_close, sigma_k, mu_annual)
    # Normalize for truncation
    return fair / p_total


# ─────────────────────────────────────────────────────────────────
# 6. Kelly fraction for position sizing
#    f* = (p×b − q) / b   where p=win prob, q=1-p, b=win/loss ratio
#    For binary contracts at price P:
#       win = (1 - P - fee)
#       loss = (P + fee)
#       b = win / loss
#    We use fractional Kelly (1/4) to limit drawdowns.
# ─────────────────────────────────────────────────────────────────

def kelly_qty(win_prob: float, entry_price: float, bankroll: float,
              fraction: float = 0.25, max_qty: int = 100) -> int:
    """Returns Kelly-optimal qty, fractionalized + capped."""
    fee = kalshi_fee(entry_price)
    win = 1.0 - entry_price - fee
    loss = entry_price + fee
    if win <= 0 or loss <= 0:
        return 0
    b = win / loss
    p = win_prob
    q = 1.0 - p
    f_full = (p * b - q) / b
    if f_full <= 0:
        return 0
    f = f_full * fraction
    dollar_size = bankroll * f
    qty = int(dollar_size / entry_price)
    return min(max(qty, 1), max_qty)


# ─────────────────────────────────────────────────────────────────
# 7. SPRT (Sequential Probability Ratio Test) for auto-disable
#    H0: win rate = p_breakeven (strategy is breakeven)
#    H1: win rate = p_target (strategy has edge)
#    We track log-likelihood ratio after each trade.
#    If LLR drops below threshold A → DISABLE that tier (no edge)
#    If LLR rises above threshold B → CONFIRMED (keep running)
# ─────────────────────────────────────────────────────────────────

# SPRT state per tier (persists in-memory; resets on bot restart)
SPRT_STATE = {
    1: {'llr': 0.0, 'p_be': 0.50, 'p_target': 0.77, 'n': 0, 'disabled': False},
    2: {'llr': 0.0, 'p_be': 0.97, 'p_target': 0.985, 'n': 0, 'disabled': False},
    3: {'llr': 0.0, 'p_be': 0.77, 'p_target': 0.79, 'n': 0, 'disabled': False},
}
# Wald boundaries for α=0.05 (false-disable rate), β=0.10 (false-confirm rate)
SPRT_LOWER = math.log(0.10 / (1 - 0.05))   # ≈ −2.25  → disable
SPRT_UPPER = math.log((1 - 0.10) / 0.05)   # ≈ +2.89  → confirmed


def sprt_record_outcome(tier: int, won: bool):
    """Update LLR after a settled trade. Disable tier if LLR drops too low."""
    st = SPRT_STATE.get(tier)
    if st is None or st['disabled']:
        return
    p1, p0 = st['p_target'], st['p_be']
    if won:
        delta = math.log(p1 / p0)
    else:
        delta = math.log((1 - p1) / (1 - p0))
    st['llr'] += delta
    st['n'] += 1
    # Only adjudicate after at least 20 trades
    if st['n'] < 20:
        return
    if st['llr'] <= SPRT_LOWER:
        st['disabled'] = True
        _log(f'SPRT: Tier {tier} AUTO-DISABLED (n={st["n"]}, llr={st["llr"]:.2f}). '
             f'Win rate below {p0*100:.0f}%.')
    elif st['llr'] >= SPRT_UPPER:
        _log(f'SPRT: Tier {tier} CONFIRMED (n={st["n"]}, llr={st["llr"]:.2f})')
        # Don't actually act — just log confirmation


def sprt_active(tier: int) -> bool:
    """Returns True if tier should still trade (not auto-disabled)."""
    return not SPRT_STATE.get(tier, {}).get('disabled', False)


def sprt_state(tier: int) -> dict:
    return dict(SPRT_STATE.get(tier, {}))


# ─────────────────────────────────────────────────────────────────
# 8. Unified fair-value function — used by Tier 2 scanner
# ─────────────────────────────────────────────────────────────────

def fair_value_robust(spot: float, strike: float, secs_to_close: float) -> Optional[dict]:
    """Returns dict with multiple fair-value estimates + a consensus.
    Used by Tier 2 instead of the original simple BS calc."""
    # Get sigma from both sources, take the MAX (more conservative)
    sigma_realized = _causal_sigma()
    sigma_ewma = ewma_sigma_annual()
    if sigma_realized is None and sigma_ewma is None:
        return None
    sigma = max(sigma_realized or 0, sigma_ewma or 0)
    # Apply floor + padding (existing logic)
    sigma = max(sigma, CFG.get('t2_sigma_floor_annual', 0.35))
    sigma_padded = sigma * (1.0 + CFG.get('sigma_uncertainty_discount', 0.50))

    mu = estimated_drift_annual()

    fair_bs = fair_yes_with_drift(spot, strike, secs_to_close, sigma_padded, mu)
    fair_jump = fair_yes_jump(spot, strike, secs_to_close, sigma_padded, mu,
                              jump_rate_per_year=CFG.get('jump_rate', 12.0),
                              jump_size_pct=CFG.get('jump_size_pct', 0.02))

    # Consensus: take MIN (most conservative) for buying YES.
    # When spot > K (deep ITM YES), both estimates should be high; the lower
    # is the floor we trust.
    fair_consensus = min(fair_bs, fair_jump)

    return {
        'sigma_realized': sigma_realized,
        'sigma_ewma': sigma_ewma,
        'sigma_used': sigma_padded,
        'mu_annual': mu,
        'fair_bs': fair_bs,
        'fair_jump': fair_jump,
        'fair': fair_consensus,
    }


print('Math models ready: EWMA σ, drift correction, jump-diffusion,')
print('                   Hampel outlier filter, Kelly sizing, SPRT auto-disable.')
