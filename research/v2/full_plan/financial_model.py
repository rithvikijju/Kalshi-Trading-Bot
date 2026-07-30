"""Year-by-year financial model: personal capital + fund AUM + revenue + costs.

Three scenarios: Bear (pessimistic), Base (realistic), Bull (optimistic).
Run from "now" (T=0, June 2026) through Year 5.

Assumes the combined sleeve produces ~18% AnnRet at Sharpe 1.8 (Base case).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# Scenarios
SCENARIOS = {
    "Bear":  dict(ann_ret=0.10, ann_vol=0.10, mdd_year=-0.12, capacity_M=50),
    "Base":  dict(ann_ret=0.18, ann_vol=0.10, mdd_year=-0.08, capacity_M=250),
    "Bull":  dict(ann_ret=0.25, ann_vol=0.10, mdd_year=-0.06, capacity_M=500),
}

# Cost model — running a fund
COSTS_BY_YEAR = {
    # Y1 — solo, paper / live small
    1: dict(legal=10000,    tech_data=15000,  ops=12000,  hires=0,           label="Solo, live small"),
    # Y2 — first LP capital, basic fund setup
    2: dict(legal=25000,    tech_data=30000,  ops=24000,  hires=80000,       label="Seed pod, 1 PT hire"),
    # Y3 — seed allocator on board
    3: dict(legal=50000,    tech_data=60000,  ops=60000,  hires=400000,      label="Pod 1, 2 FT hires"),
    # Y4 — institutional ramp
    4: dict(legal=100000,   tech_data=120000, ops=200000, hires=900000,      label="Pods 1-2, 4 hires"),
    # Y5 — multi-strat live
    5: dict(legal=200000,   tech_data=250000, ops=500000, hires=2000000,     label="Multi-pod, 8 hires"),
}

# Personal capital trajectory: solo prop trading
PERSONAL_START = 75000          # $75K personal starting capital
PERSONAL_ADD_Y1 = 25000         # add another $25K once VRP proves
PERSONAL_ADD_Y2 = 50000         # bonus from Y1 PnL re-invested

# Fund AUM trajectory (Base scenario)
AUM_BY_YEAR_BASE = {            # in $M
    1: 0,                       # year 1: only personal prop, no LP
    2: 5,                       # year 2: friends/family + first seed allocator
    3: 35,                      # year 3: seed deepens + first family office
    4: 150,                     # year 4: institutional allocators
    5: 400,                     # year 5: post 3-yr track institutional ramp
}

AUM_BY_YEAR_BEAR = {            # 50% slower than base
    1: 0, 2: 2, 3: 12, 4: 50, 5: 150,
}
AUM_BY_YEAR_BULL = {            # 60% faster than base
    1: 1, 2: 12, 3: 80, 4: 300, 5: 700,
}

AUM_TABLES = dict(Bear=AUM_BY_YEAR_BEAR, Base=AUM_BY_YEAR_BASE, Bull=AUM_BY_YEAR_BULL)

# Fee structure
MGMT_FEE = 0.02          # 2% per year on AUM
PERF_FEE = 0.20          # 20% of gross above HWM


def project_personal(scenario: str) -> pd.DataFrame:
    """Personal capital growth, solo prop trading."""
    sp = SCENARIOS[scenario]
    cap = PERSONAL_START
    rows = []
    for y in range(1, 6):
        start_cap = cap
        # year-end return = ann_ret with vol; assume realized = ann_ret (point estimate)
        ret = sp["ann_ret"]
        cap_end = start_cap * (1 + ret)
        # Personal additions
        if y == 1: cap_end += PERSONAL_ADD_Y1
        if y == 2: cap_end += PERSONAL_ADD_Y2
        # MRR = average monthly dollar PnL (gross before personal taxes)
        mrr = start_cap * ret / 12
        rows.append(dict(year=y, start_cap=start_cap, ret_pct=ret*100,
                          pnl=cap_end - start_cap, end_cap=cap_end,
                          monthly_pnl=mrr))
        cap = cap_end
    return pd.DataFrame(rows)


def project_fund(scenario: str) -> pd.DataFrame:
    """Fund AUM, fee revenue, costs, GP take-home."""
    sp = SCENARIOS[scenario]
    aum_table = AUM_TABLES[scenario]
    rows = []
    hwm = 0  # high water mark per dollar of AUM
    cumulative_gp = 0
    for y in range(1, 6):
        aum_start = aum_table[y] * 1_000_000
        # GROSS gain on average AUM (assume aum_start ≈ avg)
        gross_gain = aum_start * sp["ann_ret"]
        mgmt = aum_start * MGMT_FEE
        perf = max(0, gross_gain) * PERF_FEE
        fee_rev = mgmt + perf
        costs = sum(COSTS_BY_YEAR[y][k] for k in ["legal", "tech_data", "ops", "hires"])
        gp_take = fee_rev - costs
        cumulative_gp += gp_take
        rows.append(dict(
            year=y, aum_M=aum_table[y], gross_gain=gross_gain,
            mgmt_fee=mgmt, perf_fee=perf, total_fees=fee_rev,
            costs=costs, gp_take=gp_take, gp_cumulative=cumulative_gp,
            staff_count=1 + (y - 1) * 2 if y > 1 else 1
        ))
    return pd.DataFrame(rows)


def build_funding_plan() -> pd.DataFrame:
    """Capital required at each step + funding source."""
    steps = [
        dict(milestone="T0 - Now",                  capital_need=0,
             source="Personal savings",
             notes="$75K personal trading + $30K infra/data/legal startup"),
        dict(milestone="M0-M3 (Live small)",        capital_need=30_000,
             source="Personal", notes="Broker accts, VIX futures, data subs, legal entity"),
        dict(milestone="M3-M12 (Track record)",     capital_need=50_000,
             source="Personal", notes="Living expenses runway 9mo while building track"),
        dict(milestone="M12 (LP entity)",           capital_need=25_000,
             source="Personal", notes="Form LP/GP structure, ADV, fund admin onboarding"),
        dict(milestone="M12-M18 (Friends&family)",  capital_need=500_000,
             source="F&F LPs", notes="3-8 LPs at $50-100K each. SMA or LP structure"),
        dict(milestone="M18-M24 (Seed allocator)",  capital_need=5_000_000,
             source="Seed fund (Investcorp-Tages, NewAlpha, Borealis)",
             notes="Need 12-18mo audited track + ops infrastructure"),
        dict(milestone="Y2-Y3 (Family office)",    capital_need=25_000_000,
             source="Single family offices",
             notes="Direct intro via prime broker network or wealth manager intro"),
        dict(milestone="Y3-Y4 (Multi-family)",     capital_need=100_000_000,
             source="Multi-family offices + Fund-of-funds",
             notes="Need 3-yr audited track + risk infrastructure + compliance"),
        dict(milestone="Y4-Y5 (Institutional)",    capital_need=300_000_000,
             source="Institutional allocators (pension consultants, endowments)",
             notes="Cliffwater, Wilshire, Aksia, Albourne RFPs. 5-yr track + ops audits"),
    ]
    return pd.DataFrame(steps)


def main():
    print("=" * 60)
    print("FINANCIAL MODEL — 5-YEAR PROJECTION")
    print("=" * 60)
    out = {}
    for scen in ["Bear", "Base", "Bull"]:
        print(f"\n--- {scen} ---")
        p = project_personal(scen)
        f = project_fund(scen)
        print("Personal trading:")
        print(p[["year", "start_cap", "ret_pct", "pnl", "end_cap", "monthly_pnl"]].to_string(index=False, float_format=lambda x: f"{x:,.0f}"))
        print("\nFund:")
        print(f[["year", "aum_M", "total_fees", "costs", "gp_take", "gp_cumulative"]].to_string(index=False, float_format=lambda x: f"{x:,.0f}"))
        out[scen] = dict(personal=p, fund=f)

    print("\n--- FUNDING PLAN ---")
    fp = build_funding_plan()
    print(fp.to_string(index=False))

    # save
    for scen in out:
        out[scen]["personal"].to_parquet(f"/Users/rithvikijju/edge-bot/research/v2/full_plan/_personal_{scen}.parquet")
        out[scen]["fund"].to_parquet(f"/Users/rithvikijju/edge-bot/research/v2/full_plan/_fund_{scen}.parquet")
    fp.to_parquet("/Users/rithvikijju/edge-bot/research/v2/full_plan/_funding_plan.parquet")
    print("\nSaved all financial-model artifacts.")
    return out, fp


if __name__ == "__main__":
    main()
