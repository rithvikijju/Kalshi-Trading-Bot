"""
Convertible bond pricer — Tsiveriotis-Fernandes (TF) finite-difference implementation.

The TF model decomposes a convertible into:
    u(S, t)  total convertible value (held by investor)
    v(S, t)  "cash-only" component — the bond cash flows the issuer must pay if NOT
             converted. Subject to credit risk: discounted at r + lambda.
    u - v    equity component — subject only to risk-free rate r.

Coupled PDE system (Tsiveriotis & Fernandes 1998, "Valuing convertible bonds with credit risk"):

    du/dt + 0.5*sigma^2*S^2 * d2u/dS^2 + (r-q)*S*du/dS - r*(u-v) - (r+lambda)*v = 0
    dv/dt + 0.5*sigma^2*S^2 * d2v/dS^2 + (r-q)*S*dv/dS                    - (r+lambda)*v = 0

Solved backward from maturity with terminal payoff:
    u(S,T) = max( N + C_T,  kappa * S )
    v(S,T) = N + C_T  if conversion NOT optimal at T else 0

Free-boundary constraints applied at each timestep:
    Conversion (American):   u >= kappa * S          ; if conversion, v <- 0
    Issuer call:             u <= max(call_K, kappa*S) ; if called, v <- min(v, call_K)
    Investor put:            u >= put_K               ; if put, v <- put_K

Greeks computed by finite-difference bumps.

This is a reference implementation, not a production engine. Sanity-tested on
a no-credit / no-conversion limit (recovers a straight bond) and a deep-ITM
limit (recovers conversion ratio * stock).

Author: /goal research agent run, 2026-06-08.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class ConvertibleBond:
    """Vanilla convertible bond terms.

    All rates are continuously-compounded annualized.
    """

    face: float = 100.0         # principal at maturity
    coupon: float = 0.04        # annual coupon as fraction of face
    coupon_freq: int = 2        # payments per year
    maturity_years: float = 5.0 # T in years
    conversion_ratio: float = 5.0  # kappa: shares per bond
    call_price: float | None = None    # if not None, issuer can call at this cash price
    call_start_years: float = 0.0      # call protection ends here
    put_price: float | None = None     # if not None, investor can put at this cash price
    put_dates_years: tuple[float, ...] = ()  # American-style put windows simplified to single early-exercise


@dataclass
class MarketInputs:
    """Market state."""
    spot: float                  # current stock price
    vol: float                   # equity volatility (annualized)
    riskfree: float              # r
    div_yield: float = 0.0       # q
    credit_spread: float = 0.02  # lambda (continuously-compounded hazard rate proxy)


def _coupon_pv_dates(bond: ConvertibleBond) -> list[tuple[float, float]]:
    """Return list of (time_to_coupon_years, coupon_amount) for all coupons including
    final principal+coupon at maturity. Times measured from valuation date forward."""
    out: list[tuple[float, float]] = []
    n_total = int(round(bond.maturity_years * bond.coupon_freq))
    if n_total <= 0:
        return [(bond.maturity_years, bond.face)]
    per_coupon = bond.face * bond.coupon / bond.coupon_freq
    for i in range(1, n_total + 1):
        t = i / bond.coupon_freq
        if i < n_total:
            out.append((t, per_coupon))
        else:
            out.append((t, per_coupon + bond.face))
    return out


def price_tf(
    bond: ConvertibleBond,
    mkt: MarketInputs,
    *,
    n_s: int = 200,
    n_t: int = 500,
    s_max_mult: float = 5.0,
) -> dict:
    """Price convertible via TF finite-difference PDE.

    Returns dict with 'price', 'bond_floor', 'parity', 'conversion_premium', plus
    the full grid for further analysis.

    Numerical scheme: implicit Euler in time, central differences in S. Implicit is
    chosen for robustness near free boundaries; Crank-Nicolson can oscillate at
    the conversion barrier without rannacher smoothing.
    """

    S0 = mkt.spot
    sigma = mkt.vol
    r = mkt.riskfree
    q = mkt.div_yield
    lam = mkt.credit_spread

    T = bond.maturity_years
    F = bond.face
    kappa = bond.conversion_ratio

    # ---------- grid ----------
    s_max = max(S0 * s_max_mult, kappa and (F * s_max_mult / kappa) or s_max_mult * F)
    ds = s_max / n_s
    S = np.linspace(0.0, s_max, n_s + 1)
    dt = T / n_t
    times = np.linspace(0.0, T, n_t + 1)  # times[k] is the model time at step k

    # ---------- terminal payoff at t = T ----------
    conv_T = kappa * S
    redemption_T = F  # final coupon handled by coupon-add at maturity timestep below
    u = np.maximum(conv_T, redemption_T)
    v = np.where(redemption_T >= conv_T, redemption_T, 0.0).astype(float)

    # ---------- precompute coupon dates as step indices ----------
    coupon_dates = _coupon_pv_dates(bond)
    # We already include the final F in redemption_T, so subtract the principal piece from the maturity coupon
    coupon_steps: dict[int, float] = {}
    for t_c, c_amt in coupon_dates:
        if abs(t_c - T) < 1e-9:
            # final coupon: amount excluding principal (principal already in terminal payoff)
            extra = c_amt - F
            if extra > 1e-12:
                coupon_steps[n_t] = coupon_steps.get(n_t, 0.0) + extra
        else:
            # find nearest grid step
            k_idx = int(round(t_c / dt))
            if 0 < k_idx < n_t:
                coupon_steps[k_idx] = coupon_steps.get(k_idx, 0.0) + c_amt

    # ---------- finite-difference coefficients (interior nodes) ----------
    # Implicit scheme: (I - dt * L) V_{k} = V_{k+1} where L is the spatial operator.
    j = np.arange(1, n_s)  # interior indices
    a_u = 0.5 * dt * (sigma**2 * j**2 - (r - q) * j)
    b_u = -dt * (sigma**2 * j**2 + r)        # diagonal for u-equation: includes -r*u term
    c_u = 0.5 * dt * (sigma**2 * j**2 + (r - q) * j)

    a_v = a_u.copy()
    b_v = -dt * (sigma**2 * j**2 + (r + lam))  # v-equation has +r*v on RHS source plus -lambda*v? Re-derive:
    # The v-eq is:  dv/dt + 0.5 sigma^2 S^2 v_SS + (r-q) S v_S - (r+lambda) v = 0
    # so spatial operator on v includes -(r+lambda)*v. We subsume it into the diagonal.
    b_v = -dt * (sigma**2 * j**2 + (r + lam))

    # u-equation has a coupling term: -r*(u - v) - (r+lambda)*v = -r*u + r*v - (r+lambda)*v = -r*u - lambda*v
    # So spatial operator on u: 0.5 sigma^2 S^2 u_SS + (r-q) S u_S - r*u - lambda*v
    # Diagonal for u is therefore -dt*(sigma^2 j^2 + r), and the lambda*v coupling is a source term.

    # Build tridiagonal matrices once
    def build_tridiag(a_arr, b_arr, c_arr):
        n = len(b_arr)
        diag = 1.0 - b_arr
        lower = -a_arr[1:]
        upper = -c_arr[:-1]
        return diag, lower, upper

    diag_u, lower_u, upper_u = build_tridiag(a_u, b_u, c_u)
    diag_v, lower_v, upper_v = build_tridiag(a_v, b_v, c_v := c_u.copy())  # same as u for now since (r-q) same

    def solve_tridiag(diag, lower, upper, rhs):
        n = len(rhs)
        cp = np.zeros(n)
        dp = np.zeros(n)
        cp[0] = upper[0] / diag[0]
        dp[0] = rhs[0] / diag[0]
        for i in range(1, n):
            m = diag[i] - lower[i - 1] * cp[i - 1] if i - 1 < len(lower) else diag[i]
            if i - 1 < len(upper) - 1 + 1:
                pass
            denom = diag[i] - (lower[i - 1] * cp[i - 1] if i - 1 < len(lower) else 0.0)
            if i < n - 1:
                cp[i] = upper[i] / denom if i < len(upper) else 0.0
            dp[i] = (rhs[i] - (lower[i - 1] * dp[i - 1] if i - 1 < len(lower) else 0.0)) / denom
        x = np.zeros(n)
        x[-1] = dp[-1]
        for i in range(n - 2, -1, -1):
            x[i] = dp[i] - (cp[i] * x[i + 1] if i < len(cp) else 0.0)
        return x

    # ---------- backward time-stepping ----------
    for k in range(n_t - 1, -1, -1):
        # 1. Add coupon if this step is a coupon date (cash payment to investor)
        if (k + 1) in coupon_steps:
            c_amt = coupon_steps[k + 1]
            u += c_amt
            v += c_amt

        # 2. Build RHS for u-equation. Coupling source: -dt * lambda * v (interior nodes)
        rhs_u = u[1:-1].copy()
        rhs_u -= dt * lam * v[1:-1]

        # Apply Dirichlet BCs:
        #   S=0: u = v at this boundary equals PV of remaining cash flows on risky curve
        #        Already encoded in u[0], v[0] from previous step. For simplicity,
        #        roll them forward as: u[0] *= exp(-(r+lam)*dt), v[0] *= exp(-(r+lam)*dt)
        u[0] *= math.exp(-(r + lam) * dt)
        v[0] *= math.exp(-(r + lam) * dt)
        #   S=s_max: u ≈ kappa*S (conversion certain), v ≈ 0
        u[-1] = kappa * S[-1]
        v[-1] = 0.0

        # Adjust RHS for boundary terms
        rhs_u[0] += a_u[0] * u[0]
        rhs_u[-1] += c_u[-1] * u[-1]

        # Solve u-equation
        new_u = solve_tridiag(diag_u, lower_u, upper_u, rhs_u)
        u_new = np.concatenate([[u[0]], new_u, [u[-1]]])

        # Build RHS for v-equation (no coupling)
        rhs_v = v[1:-1].copy()
        rhs_v[0] += a_v[0] * v[0]
        rhs_v[-1] += c_v[-1] * v[-1]
        new_v = solve_tridiag(diag_v, lower_v, upper_v, rhs_v)
        v_new = np.concatenate([[v[0]], new_v, [v[-1]]])

        # 3. Apply free-boundary constraints
        t_now = times[k]

        # Conversion (American): u must be >= kappa*S; if equal, v drops to 0 (no bond claim if converted)
        convert_mask = u_new < kappa * S
        u_new = np.where(convert_mask, kappa * S, u_new)
        v_new = np.where(convert_mask, 0.0, v_new)

        # Issuer call: if t_now >= call_start_years and bond.call_price is set
        if bond.call_price is not None and t_now >= bond.call_start_years:
            # Issuer pays max(call_price, kappa*S) -- investor will convert if conversion > call
            call_ceiling = np.maximum(bond.call_price, kappa * S)
            call_mask = u_new > call_ceiling
            u_new = np.where(call_mask, call_ceiling, u_new)
            v_new = np.where(call_mask & (kappa * S < bond.call_price),
                             np.minimum(v_new, bond.call_price), v_new)
            v_new = np.where(call_mask & (kappa * S >= bond.call_price), 0.0, v_new)

        # Investor put: simplified to "always puttable if put_price is set" — refine for real terms
        if bond.put_price is not None:
            put_mask = u_new < bond.put_price
            u_new = np.where(put_mask, bond.put_price, u_new)
            v_new = np.where(put_mask, bond.put_price, v_new)

        u = u_new
        v = v_new

    # ---------- interpolate to spot ----------
    price = float(np.interp(S0, S, u))
    bond_floor = float(np.interp(S0, S, v))
    parity = kappa * S0

    return {
        "price": price,
        "bond_floor": bond_floor,
        "parity": parity,
        "conversion_premium_pct": (price / parity - 1.0) * 100.0 if parity > 0 else float("nan"),
        "S_grid": S,
        "u_grid": u,
        "v_grid": v,
        "n_s": n_s,
        "n_t": n_t,
    }


def implied_vol(
    bond: ConvertibleBond,
    mkt: MarketInputs,
    market_price: float,
    *,
    lo: float = 0.05,
    hi: float = 2.0,
    tol: float = 1e-3,
    max_iter: int = 40,
    n_s: int = 150,
    n_t: int = 300,
) -> float:
    """Bisection for the equity vol that recovers the market price under the TF model.

    Returns the convert-implied equity vol. Compare to listed-option implied vol and
    realized vol — gap is the RV signal.
    """
    def f(sig):
        m = MarketInputs(spot=mkt.spot, vol=sig, riskfree=mkt.riskfree,
                         div_yield=mkt.div_yield, credit_spread=mkt.credit_spread)
        return price_tf(bond, m, n_s=n_s, n_t=n_t)["price"] - market_price

    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        # No sign change — return the closer endpoint
        return lo if abs(f_lo) < abs(f_hi) else hi
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if abs(fm) < tol:
            return mid
        if fm * f_lo < 0:
            hi = mid
            f_hi = fm
        else:
            lo = mid
            f_lo = fm
    return 0.5 * (lo + hi)


def greeks(
    bond: ConvertibleBond,
    mkt: MarketInputs,
    *,
    n_s: int = 150,
    n_t: int = 300,
    bumps: dict | None = None,
) -> dict:
    """Compute Greeks by central finite differences (bump and revalue).

    Greeks returned:
        delta              dV/dS (shares-equivalent equity hedge per bond)
        gamma              d2V/dS2
        vega               dV/d(sigma)  per 1.00 vol point (0.01 = 1%)
        theta              dV/dt        per year (negative if decay)
        rho                dV/dr        per 1.00 of rate (0.01 = 100bp)
        credit_delta       dV/d(lambda) per 1.00 of spread (0.01 = 100bp)
    """
    bumps = bumps or {}
    ds = bumps.get("ds", mkt.spot * 0.01)
    dvol = bumps.get("dvol", 0.01)
    dr = bumps.get("dr", 0.0001)
    dlam = bumps.get("dlam", 0.0001)
    dt = bumps.get("dt", 1.0 / 365.0)

    def p(spot=None, vol=None, r=None, lam=None, T=None):
        m = MarketInputs(
            spot=mkt.spot if spot is None else spot,
            vol=mkt.vol if vol is None else vol,
            riskfree=mkt.riskfree if r is None else r,
            div_yield=mkt.div_yield,
            credit_spread=mkt.credit_spread if lam is None else lam,
        )
        b = ConvertibleBond(
            face=bond.face, coupon=bond.coupon, coupon_freq=bond.coupon_freq,
            maturity_years=bond.maturity_years if T is None else T,
            conversion_ratio=bond.conversion_ratio,
            call_price=bond.call_price, call_start_years=bond.call_start_years,
            put_price=bond.put_price, put_dates_years=bond.put_dates_years,
        )
        return price_tf(b, m, n_s=n_s, n_t=n_t)["price"]

    base = p()
    p_up_s = p(spot=mkt.spot + ds)
    p_dn_s = p(spot=mkt.spot - ds)
    p_up_v = p(vol=mkt.vol + dvol)
    p_up_r = p(r=mkt.riskfree + dr)
    p_dn_r = p(r=mkt.riskfree - dr)
    p_up_l = p(lam=mkt.credit_spread + dlam)
    p_up_t = p(T=max(1e-3, bond.maturity_years - dt))

    return {
        "price": base,
        "delta": (p_up_s - p_dn_s) / (2.0 * ds),
        "gamma": (p_up_s - 2.0 * base + p_dn_s) / (ds * ds),
        "vega": (p_up_v - base) / dvol,
        "theta": (p_up_t - base) / dt,  # per year; negative = decay
        "rho": (p_up_r - p_dn_r) / (2.0 * dr),
        "credit_delta": (p_up_l - base) / dlam,
    }


# ----------------------------- demonstration & sanity tests -----------------------------

def _sanity_check_straight_bond() -> None:
    """Limit test: tiny conversion ratio + zero credit spread → recovers straight bond PV."""
    bond = ConvertibleBond(
        face=100.0, coupon=0.05, coupon_freq=2, maturity_years=3.0,
        conversion_ratio=0.001,  # effectively never optimal to convert
    )
    mkt = MarketInputs(spot=50.0, vol=0.30, riskfree=0.05, div_yield=0.0, credit_spread=0.0)
    out = price_tf(bond, mkt, n_s=120, n_t=200)
    # Straight bond PV at r=5% continuous, semiannual 5% coupons (2.5 every 6mo) + 100 at maturity
    cf = _coupon_pv_dates(bond)
    pv = sum(c * math.exp(-0.05 * t) for t, c in cf)
    diff = abs(out["price"] - pv) / pv * 100.0
    print(f"[sanity] straight-bond limit: TF={out['price']:.3f}, analytic PV={pv:.3f}, diff={diff:.2f}%")
    assert diff < 5.0, "TF model should recover straight-bond PV within 5% in the no-conversion / no-credit limit"


def _sanity_check_deep_itm() -> None:
    """Limit test: deep ITM (S >> bond floor / kappa) → price ≈ kappa * S."""
    bond = ConvertibleBond(
        face=100.0, coupon=0.04, coupon_freq=2, maturity_years=2.0,
        conversion_ratio=5.0,
    )
    mkt = MarketInputs(spot=200.0, vol=0.40, riskfree=0.04, div_yield=0.0, credit_spread=0.03)
    out = price_tf(bond, mkt, n_s=200, n_t=300)
    parity = bond.conversion_ratio * mkt.spot
    diff_pct = (out["price"] / parity - 1.0) * 100.0
    print(f"[sanity] deep-ITM limit: price={out['price']:.2f}, parity={parity:.2f}, premium={diff_pct:.2f}%")
    assert abs(diff_pct) < 5.0, "Deep-ITM convert should converge to parity within 5%"


def _demo_rv_signal() -> None:
    """Show the cheapness signal workflow on a synthetic convert.

    Setup: imagine a real convert trades at $98 in the market with these terms.
    Compute the convert-implied equity vol. Compare to (a) listed-option ATM vol
    and (b) realized vol. The gap is the RV signal.
    """
    print("\n=== Synthetic RV signal demo ===")
    bond = ConvertibleBond(
        face=100.0, coupon=0.025, coupon_freq=2, maturity_years=4.0,
        conversion_ratio=4.0, call_price=130.0, call_start_years=2.0,
    )
    mkt = MarketInputs(spot=28.0, vol=0.45, riskfree=0.045, div_yield=0.0, credit_spread=0.04)

    fair = price_tf(bond, mkt, n_s=180, n_t=400)
    print(f"Inputs: S={mkt.spot}, model vol={mkt.vol*100:.0f}%, credit={mkt.credit_spread*100:.0f}%, T={bond.maturity_years}y")
    print(f"Fair value @ vol={mkt.vol*100:.0f}%: {fair['price']:.2f}")
    print(f"  bond floor    : {fair['bond_floor']:.2f}")
    print(f"  parity        : {fair['parity']:.2f}")
    print(f"  premium       : {fair['conversion_premium_pct']:.2f}%")

    # Pretend market price is $98 (below model fair). Compute convert-implied vol:
    mkt_price = 98.0
    iv = implied_vol(bond, mkt, mkt_price, lo=0.10, hi=1.20)
    print(f"\nMarket price {mkt_price:.2f} → convert-implied vol = {iv*100:.1f}%")
    print(f"If listed-option ATM IV = 50%, convert is trading at {(iv/0.50 - 1)*100:+.1f}% of listed vol")

    print("\nGreeks:")
    g = greeks(bond, mkt, n_s=130, n_t=260)
    print(f"  delta        : {g['delta']:.3f}  (shares to short per bond)")
    print(f"  gamma        : {g['gamma']:.4f}")
    print(f"  vega (per 1%): {g['vega']*0.01:.3f}")
    print(f"  theta (1d)   : {g['theta']/365.0:.3f}")
    print(f"  rho (per bp) : {g['rho']*0.0001:.4f}")
    print(f"  credit (1bp) : {g['credit_delta']*0.0001:.4f}")


if __name__ == "__main__":
    _sanity_check_straight_bond()
    _sanity_check_deep_itm()
    _demo_rv_signal()
