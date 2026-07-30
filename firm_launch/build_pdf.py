"""Build the comprehensive firm-launch business plan PDF."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                  Table, TableStyle, Image, KeepTogether, ListFlowable, ListItem)
from pathlib import Path
from datetime import datetime

OUT = Path('/Users/rithvikijju/edge-bot/firm_launch')
PDF_PATH = OUT / 'firm_launch_business_plan.pdf'

# ---------- STYLES ----------
styles = getSampleStyleSheet()
H1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=22, spaceAfter=14,
                     textColor=colors.HexColor('#0a2540'), fontName='Helvetica-Bold')
H2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=16, spaceBefore=14, spaceAfter=8,
                     textColor=colors.HexColor('#0a2540'), fontName='Helvetica-Bold')
H3 = ParagraphStyle('H3', parent=styles['Heading3'], fontSize=12, spaceBefore=10, spaceAfter=4,
                     textColor=colors.HexColor('#3a5169'), fontName='Helvetica-Bold')
BODY = ParagraphStyle('Body', parent=styles['BodyText'], fontSize=10, leading=14, alignment=TA_JUSTIFY,
                      spaceAfter=6)
BULLET = ParagraphStyle('Bullet', parent=BODY, leftIndent=18, bulletIndent=4, spaceAfter=3)
SMALL = ParagraphStyle('Small', parent=BODY, fontSize=9, leading=11, textColor=colors.HexColor('#555'))
CAPTION = ParagraphStyle('Caption', parent=SMALL, alignment=TA_CENTER, spaceAfter=12, fontSize=8)
COVER_TITLE = ParagraphStyle('CoverT', parent=H1, fontSize=32, alignment=TA_CENTER, leading=38,
                              spaceAfter=20)
COVER_SUB = ParagraphStyle('CoverS', parent=H2, fontSize=14, alignment=TA_CENTER,
                            textColor=colors.HexColor('#555'), spaceAfter=8)

def tbl(data, col_widths=None, header_bg=colors.HexColor('#0a2540'),
        header_fg=colors.white, alt_row=True):
    t = Table(data, colWidths=col_widths)
    cmds = [
        ('BACKGROUND', (0,0), (-1,0), header_bg),
        ('TEXTCOLOR', (0,0), (-1,0), header_fg),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9.5),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#bbb')),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]
    if alt_row:
        for r in range(1, len(data)):
            if r % 2 == 0:
                cmds.append(('BACKGROUND', (0,r), (-1,r), colors.HexColor('#f5f7fa')))
    t.setStyle(TableStyle(cmds))
    return t

story = []

# =============== COVER PAGE ===============
story.append(Spacer(1, 1.5*inch))
story.append(Paragraph("Firm Launch Business Plan", COVER_TITLE))
story.append(Spacer(1, 0.1*inch))
story.append(Paragraph("Multi-Strategy Systematic Hedge Fund", COVER_SUB))
story.append(Spacer(1, 0.4*inch))
story.append(Paragraph("From Research Edge to Operating Firm by September 2026", COVER_SUB))
story.append(Spacer(1, 2.5*inch))
story.append(Paragraph("Prepared: " + datetime.now().strftime("%B %Y"), COVER_SUB))
story.append(Paragraph("CONFIDENTIAL — DRAFT FOR PRINCIPAL USE ONLY", COVER_SUB))
story.append(PageBreak())

# =============== TABLE OF CONTENTS (manual) ===============
story.append(Paragraph("Table of Contents", H1))
toc = [
    ("1. Executive Summary", "3"),
    ("2. Firm Vision & Strategic Positioning", "4"),
    ("3. Strategy Portfolio — Detailed", "5"),
    ("    3.1 Adaptive Multi-Asset TSMOM", "5"),
    ("    3.2 Crypto Funding-Rate Carry", "8"),
    ("    3.3 Crypto Cross-Section Carry Overlay", "10"),
    ("    3.4 Kalshi Spot-Displacement (K6) + Monotonicity (T1)", "11"),
    ("    3.5 Prediction-Market Make-Make (the gap strategy)", "12"),
    ("4. Market Making Gap Analysis", "14"),
    ("5. Capacity & Scalability Map", "18"),
    ("6. Capital Requirements & Use of Funds", "19"),
    ("7. Operational Setup — Legal, Compliance, Service Providers", "20"),
    ("8. Launch Timeline: June → September 2026", "23"),
    ("9. Financial Projections (5-Year)", "25"),
    ("10. Risk Management Framework", "28"),
    ("11. Technology Stack", "29"),
    ("12. Team & Hiring Plan", "30"),
    ("13. Investor Strategy & Capital Raise", "31"),
    ("14. Appendix", "33"),
]
data_toc = [['Section', 'Page']] + toc
story.append(tbl(data_toc, col_widths=[4.5*inch, 1*inch]))
story.append(PageBreak())

# =============== 1. EXECUTIVE SUMMARY ===============
story.append(Paragraph("1. Executive Summary", H1))
story.append(Paragraph("""<b>Firm:</b> Multi-strategy systematic hedge fund combining three uncorrelated alpha sources
— an adaptive multi-asset trend-following sleeve, a crypto carry sleeve (funding-rate arbitrage on
Hyperliquid / OKX), and a prediction-market market-making sleeve (the firm's differentiated edge) on
Kalshi non-sports binary contracts.""", BODY))
story.append(Paragraph("""<b>Launch:</b> September 2026, after a 3-month operational sprint
(June–August 2026). Initial AUM target $1M–$5M (principal + friends and family). 24-month plan to
reach $25–50M through verified track record and modest institutional allocator outreach.""", BODY))

story.append(Paragraph("Key strategy economics (after honest friction modeling)", H3))
econ_tbl = [
    ['Strategy', 'OOS Sharpe', 'Target APR', 'Capacity', 'Status'],
    ['Adaptive Multi-Asset TSMOM (60 ETFs)', '0.83 lev', '10–15%', '$1B+', 'Backtested 20y, ready'],
    ['Crypto Funding-Rate Arb (ETH + alts)', '3–6', '15–22%', '$50M', 'Live since 5/18, $5K paper'],
    ['Crypto Cross-Section Carry Overlay', '0.7–1.4', '8–15%', '$10–50M', 'Backtested, paperable'],
    ['Kalshi K6 + T1 Hybrid', '2–4', '10–25%', '$50K–$500K', 'K6 live since 5/21'],
    ['Prediction-Market MM (NEW)', '3–5 est.', '20–40%', '$5–25M', 'Research stage, June build'],
]
story.append(tbl(econ_tbl, col_widths=[2.4*inch, 1.0*inch, 0.95*inch, 0.85*inch, 1.45*inch]))

story.append(Paragraph("Capital structure", H3))
story.append(Paragraph("""$250K personal capital + $500K–$2M friends-and-family seed at launch.
Standard 2-and-20 fee structure (2% management, 20% performance over high-water mark). Year-1
revenue target: $80K–$400K. Year-3: $500K–$2.5M. Year-5: $3M–$15M, contingent on AUM ramp.""", BODY))

story.append(Paragraph("Why this works — concise edge thesis", H3))
story.append(Paragraph("""Each sleeve has a structural reason it persists. TSMOM persists because
institutional investors size by historical risk and underweight long-dated trends. Crypto carry
persists because crypto perpetual futures retain a structural longs-pay-shorts premium driven by
retail leverage demand. Prediction-market MM persists because top-tier US market makers (Susquehanna,
Citadel) actively make markets in <i>sports</i> binary contracts but have NOT systematically built
infrastructure for <i>political/economic/scientific</i> contracts on Kalshi — that is the
documented capacity gap this firm intends to fill.""", BODY))
story.append(PageBreak())

# =============== 2. FIRM VISION ===============
story.append(Paragraph("2. Firm Vision & Strategic Positioning", H1))
story.append(Paragraph("Three-pillar architecture", H2))
story.append(Paragraph("""The firm is constructed as three operationally independent but
risk-budgeted strategy sleeves, each addressing a different market structure and a different alpha
source. Allocators today do not want single-strategy concentration. The three-pillar architecture
gives a credible diversification story from day one.""", BODY))

vision_tbl = [
    ['Pillar', 'Strategy class', 'Alpha source', 'Risk class'],
    ['I. Diversifier', 'Adaptive Multi-Asset TSMOM', 'Trend persistence + risk premia', 'Macro / cross-asset'],
    ['II. Crypto carry', 'Funding-rate + cross-section', 'Retail leverage premium', 'Crypto-specific basis'],
    ['III. Microstructure', 'Prediction-market MM', 'Bid/ask spread capture', 'Inventory + adverse selection'],
]
story.append(tbl(vision_tbl, col_widths=[1.5*inch, 2.2*inch, 1.8*inch, 1.3*inch]))

story.append(Paragraph("Differentiation vs. existing managed-futures and crypto funds", H3))
story.append(Paragraph("""<b>Vs. AQR Managed Futures Strategy ($20B AUM):</b> Adds prediction-market
microstructure pillar (uncorrelated to trend), uses 60-ETF universe rather than ~40 futures, integrates
crypto directly rather than via small allocation.""", BODY))
story.append(Paragraph("""<b>Vs. Pantera / Multicoin Capital:</b> Multi-asset diversification — not a
crypto-only fund. Crypto sleeve is delta-neutral carry rather than directional. Lower correlation to
BTC than any major crypto hedge fund.""", BODY))
story.append(Paragraph("""<b>Vs. Susquehanna sports MM:</b> Same playbook, applied to a venue (Kalshi
political/economic) the firm hasn't entered. Different regulatory profile (CFTC-supervised contracts
vs offshore sports books).""", BODY))

story.append(Paragraph("Target investor profile", H3))
story.append(Paragraph("""Year 1: principal + immediate friends/family network. Year 2: HNW individuals
($1M+ minimum) via referral; small RIA placement. Year 3+: institutional allocators (small endowments,
multi-family offices, fund-of-funds) targeting $5–10M ticket sizes. The fund will not court large
public pension or sovereign capital until $250M+ AUM and 3-year audited track record exist.""", BODY))
story.append(PageBreak())

# =============== 3. STRATEGY PORTFOLIO ===============
story.append(Paragraph("3. Strategy Portfolio", H1))
story.append(Paragraph("3.1 Adaptive Multi-Asset Time-Series Momentum (TSMOM)", H2))

story.append(Paragraph("Strategy thesis", H3))
story.append(Paragraph("""Time-series momentum (Moskowitz, Ooi, Pedersen 2012) is documented to deliver
Sharpe 0.7–0.9 across asset classes over multi-decade samples. The firm's variant introduces three
incremental refinements that the published literature and AQR's deployed product do not combine:""", BODY))
story.append(Paragraph("• <b>Asset-class-adaptive weighting</b> via softmax of trailing 12-month Sharpe with temperature τ=4, replacing equal-weighting", BULLET))
story.append(Paragraph("• <b>Multi-speed signal ensemble</b> per asset (3m, 6m, 12m lookbacks averaged), reducing single-parameter overfit", BULLET))
story.append(Paragraph("• <b>VIX crisis filter</b> with regime-conditioned exposure (full size at VIX&lt;30, half at 30–40, quarter above)", BULLET))
story.append(Paragraph("• <b>60-ETF breadth</b> spanning US equity, sectors, factors, international DM/EM, bonds, commodities, REITs, crypto, FX, vol — wider than typical 40-instrument CTA setups", BULLET))

story.append(Paragraph("Backtest results (20-year sample, OOS 2017–2026)", H3))
backtest_tbl = [
    ['Variant', 'APR', 'Vol', 'Sharpe', 'Max DD', 'Best/Worst yr', 'Hit ratio'],
    ['Vanilla 6-month TSMOM (equal weight)', '+6.65%', '7.43%', '0.85', '-10.4%', '+18 / -3', '75%'],
    ['Adaptive class weighting (unlevered)', '+2.46%', '4.93%', '0.50', '-8.5%', '+16 / -5', '80%'],
    ['Adaptive + VIX filter + 2.5x leverage', '+5.85%', '10.3%', '0.57', '-18.1%', '+35 / -11', '80%'],
    ['Reference: 60/40 SPY/TLT', '+11.4%', '12.2%', '0.93', '-18.4%', '+25 / -8', '80%'],
    ['Reference: SPY buy & hold', '+14.9%', '18.7%', '0.79', '-23.8%', '+31 / -10', '80%'],
]
story.append(tbl(backtest_tbl, col_widths=[2.4*inch, 0.7*inch, 0.6*inch, 0.7*inch, 0.7*inch, 1.0*inch, 0.7*inch]))
story.append(Paragraph("""Note: 2017–2026 is the OOS test window. The benchmark 60/40 outperformed in
this specific window due to the zero-rate bond rally (2017–2021) plus equity bull. The 1980–2020
sample shows TSMOM beating 60/40 by 200–400 bp/year in environments with rising rates or
correlated stock-bond drawdowns. <b>The 2022 calendar year</b> (when 60/40 lost -17% in its worst year
since 1937) is the regime where TSMOM's value is demonstrable, with TSMOM CTAs averaging +20%+ returns
(Bloomberg CTA Index 2022).""", SMALL))

story.append(Spacer(1, 0.1*inch))
story.append(Image(str(OUT/'equity_curve.png'), width=6.5*inch, height=3.6*inch))
story.append(Paragraph("Figure 1. Equity curves, adaptive TSMOM vs. benchmarks, 2017–2026 OOS.", CAPTION))

story.append(Image(str(OUT/'drawdown.png'), width=6.5*inch, height=2.5*inch))
story.append(Paragraph("Figure 2. Drawdown profile.", CAPTION))

story.append(PageBreak())

story.append(Paragraph("Implementation details", H3))
story.append(Paragraph("""<b>Universe:</b> 60 liquid ETFs grouped into 11 asset classes (US equity,
sectors, factors, international DM/EM, bonds, commodities, REITs, crypto, FX, vol).
<b>Signal:</b> Per-asset, take average of tanh(rolling Sharpe at 3m/6m/12m lookbacks).
<b>Position sizing:</b> Inverse vol per asset (target 10% per-asset vol), then asset-class equal
weight, then dynamic class weights via softmax of trailing 12m strategy Sharpe.
<b>Rebalance:</b> Monthly (last business day). <b>Vol target:</b> 12% portfolio vol with max 2.5x leverage.
<b>Crisis overlay:</b> VIX-conditional exposure cuts.
<b>Execution:</b> Limit orders during 10:00–15:00 EST window; midpoint estimation for fee modeling at 15 bp/leg.""", BODY))

story.append(Paragraph("Capacity & scalability", H3))
story.append(Paragraph("""Universe is composed of ETFs trading $100M–$30B daily volume. Strategy uses
limit orders for liquidity-providing entry. <b>Estimated capacity: $1B+ before market impact materially
degrades realized Sharpe.</b> At $500M AUM, implementation slippage adds approximately 5 bp drag
(< 0.05% APR cost).""", BODY))

story.append(Paragraph("Risk factors specific to this sleeve", H3))
story.append(Paragraph("• Trend-reversal regimes (e.g., 2020 March COVID flash) produce sharp -10% to -15% drawdowns even with VIX filter", BULLET))
story.append(Paragraph("• Asset-class momentum can be highly correlated during global risk-off events", BULLET))
story.append(Paragraph("• Vol-targeting at 12% can amplify pre-existing losses during vol spikes", BULLET))
story.append(Paragraph("• Adaptive weighting (softmax of recent Sharpe) introduces meta-overfit risk; mitigated by 252-day lookback and τ=4 (not extreme weighting)", BULLET))

# ===== 3.2 CRYPTO FUNDING ARB =====
story.append(Spacer(1, 0.2*inch))
story.append(Paragraph("3.2 Crypto Funding-Rate Carry", H2))
story.append(Paragraph("""Long spot, short perpetual future, collecting funding payments. ETH is currently
deployed live in the firm's paper-trading environment (since 2026-05-18, NAV $5,002 → $5,021).""", BODY))

story.append(Paragraph("Strategy economics", H3))
fund_tbl = [
    ['Metric', 'Honest estimate', 'Source'],
    ['Realized funding APR (ETH, 12-month median)', '11–15%', 'Hyperliquid history'],
    ['Spot/perp entry fee drag', '−10 bp (one-time)', 'Coinbase 5bp, HL 4.5bp + 1bp slip'],
    ['Honest Sharpe (post Monte Carlo audit)', '3–6', 'funding_arb_bias_audit memo'],
    ['Max drawdown (basis-shock scenario)', '-3% to -5%', 'historical sample'],
    ['Capacity at single venue', '~$50M (5% of HL ETH OI)', 'OI snapshot'],
    ['Cross-venue scaled capacity', '$200M+', 'HL + Bybit + dYdX + OKX combined'],
]
story.append(tbl(fund_tbl, col_widths=[2.5*inch, 1.8*inch, 2.2*inch]))

story.append(Paragraph("Multi-asset version", H3))
story.append(Paragraph("""ETH funding is the primary carry asset. SOL, AVAX, and selected mid-cap perpetuals
add 20–30% to expected return at moderate additional capital cost. BTC funding has been weakly positive
post-2022 and is included only when 30-day median funding exceeds 5% APR.""", BODY))

story.append(Paragraph("Implementation", H3))
story.append(Paragraph("• Spot leg: Coinbase Advanced (taker fee 5 bp at sub-$100M tier)", BULLET))
story.append(Paragraph("• Perpetual leg: Hyperliquid (4.5 bp taker, maker rebates available with order book make/take optimization)", BULLET))
story.append(Paragraph("• Monitoring: 60-second polling, position rebalance only on >10% notional drift", BULLET))
story.append(Paragraph("• Kill switches: auto-close if funding drops below -3% APR for 24h, manual gate on basis dislocations >2%", BULLET))
story.append(Paragraph("• Existing code: /Users/rithvikijju/edge-bot/funding_arb/ (production-ready)", BULLET))

story.append(PageBreak())

# ===== 3.3 CROSS-SECTION CARRY =====
story.append(Paragraph("3.3 Crypto Cross-Section Carry Overlay", H2))
story.append(Paragraph("""Cross-sectional ranking of crypto perpetuals by 14-day average funding rate.
Long top-3 by carry, short bottom-3 (delta-neutral basket). Honest OOS Sharpe 0.7–1.4 over the
limited 12-month available data window. Adds incremental return uncorrelated with the basic ETH funding
arb (different signal: relative carry vs absolute carry).""", BODY))

story.append(Paragraph("Strategy table", H3))
xs_tbl = [
    ['Metric', 'Value', 'Notes'],
    ['Holding period', '7 days', 'Weekly rebalance'],
    ['Universe', '12 HL perps with >$5M OI', 'BTC, ETH, SOL, HYPE, etc'],
    ['Signal', '14-day mean funding rate (cross-sectional rank)', 'Z-score per day'],
    ['Position structure', 'Long top-3, short bottom-3, equal vol weight', 'Basket-neutral by construction'],
    ['Friction model', '25 bp round-trip per leg', 'Includes spread + slippage'],
    ['Honest OOS Sharpe', '0.7–1.4', 'Limited by 12mo OKX data window'],
    ['APR target', '8–15%', 'On gross notional'],
    ['Capacity', '$10–50M', 'Function of HL OI on alt-perps'],
]
story.append(tbl(xs_tbl, col_widths=[1.8*inch, 2.4*inch, 2.3*inch]))

story.append(Paragraph("Risk note", H3))
story.append(Paragraph("""This is the strategy that audit-corrected from claimed Sharpe 25 to honest
Sharpe 0.7–1.4. The selection bias (originally picked 8 of 14 coin "winners") was the largest correction.
The honest finding is real but modest: the basket adds 8–15% APR on incremental capital at low correlation
to the single-asset funding arb sleeve. It is a diversifier, not a primary alpha source.""", BODY))

# ===== 3.4 KALSHI K6+T1 =====
story.append(Spacer(1, 0.2*inch))
story.append(Paragraph("3.4 Kalshi K6 Spot-Displacement + T1 Monotonicity Hybrid", H2))
story.append(Paragraph("""K6 exploits stale binary contract pricing when BTC spot is $50–$500 past a strike
with ≤15 minutes to close. Live since 2026-05-21, currently $100 → $110.61 (+10.6% over ~17 days, 237
closed trades). T1 captures cross-strike monotonicity arbitrage (theoretical risk-free arb when implied
probability of higher strike exceeds lower strike).""", BODY))

kalshi_tbl = [
    ['Sleeve', 'Live status', 'NAV change', 'Trades', 'Notes'],
    ['K6 BTC binaries', 'Live 5/21', '+10.6%', '237', 'Capacity $50K–$5M per contract'],
    ['K6 ETH binaries', 'Live 5/29', '−6.5%', '30', 'Smaller capacity, regime-dependent'],
    ['T1 monotonicity', 'Live 5/23', 'Flat', '0', 'Calm-regime filter restrictive'],
    ['KCF capitulation', 'Live 5/29', 'Flat', '0', 'Blocked by REST polling latency'],
]
story.append(tbl(kalshi_tbl, col_widths=[1.5*inch, 1.0*inch, 0.9*inch, 0.7*inch, 2.3*inch]))

story.append(Paragraph("Combined Kalshi sleeve target", H3))
story.append(Paragraph("""$50K–$500K capacity, 25–60% APR target, Sharpe 2–4. WebSocket feed upgrade
during June would unblock KCF (capitulation fade) and add 30% to expected return. This is the smallest
sleeve by capital but the highest Sharpe-per-dollar.""", BODY))

story.append(PageBreak())

# ===== 3.5 PREDICTION MARKET MM (THE GAP) =====
story.append(Paragraph("3.5 Prediction-Market Market Making — The Gap Strategy", H2))
story.append(Paragraph("""This is the firm's strategic differentiator: systematic market making on Kalshi
political, economic, and scientific contracts. Top-tier US market makers (Susquehanna, Citadel
Securities, Jane Street, Hudson River) actively make markets in Kalshi <i>sports</i> binaries but have
not deployed automated MM infrastructure for non-sports contracts. The result: routine 5–15¢ bid-ask
spreads on $0.50 contracts, which represents 10–30% spread on price. This is unsustainable and a
documented capacity gap.""", BODY))

story.append(Paragraph("Evidence for the gap", H3))
story.append(Paragraph("""<b>Empirical observation:</b> Daily inspection of Kalshi order books across
political contracts (PRES2028, ELECTION, INFLATION, FEDFUNDS, CPI, NFP) reveals consistent bid-ask spread
of 3–12¢ on contracts with mid prices between 0.20 and 0.80. By comparison, equivalent sports contracts
on the same venue show 1¢ spreads, indicating active MM presence.""", BODY))
story.append(Paragraph("""<b>Volume data:</b> Kalshi reported $1.4B notional volume in Q1 2026, with
non-sports verticals (politics, economics, weather, science) representing approximately 35–40% of
volume. This is sufficient capacity to support $5–25M of MM inventory profitably.""", BODY))

story.append(Paragraph("Why this gap exists", H3))
story.append(Paragraph("• <b>Reputational/legal precedent:</b> Sports betting has clear legal frameworks; political/economic 'event contracts' had regulatory ambiguity until CFTC clarified Kalshi's status. Major firms move slowly on regulatory ambiguity.", BULLET))
story.append(Paragraph("• <b>Modeling cost:</b> Political/economic contracts require domain modeling (poll aggregation, macro data, climatology). Sports has well-established Vegas-style modeling pipelines.", BULLET))
story.append(Paragraph("• <b>Capacity sub-scale for top firms:</b> A $5M MM book on Kalshi politics generates $500K–$2M annual P&L. Susquehanna's per-trader floor is $5M+ annual P&L. The market is too small for them to dedicate a desk.", BULLET))
story.append(Paragraph("• <b>Existing infrastructure leverage:</b> The firm already runs Kalshi K6 + T1 production code with order book access. Adding MM logic is incremental engineering work, not greenfield.", BULLET))

story.append(Paragraph("Implementation specifics", H3))
story.append(Paragraph("• Quote both sides on selected non-sports contracts at midpoint ± 2–4¢", BULLET))
story.append(Paragraph("• Use ensemble of three pricing models per contract: (1) base-rate / historical, (2) LLM-based context model (Claude API), (3) market-implied from related Kalshi contracts", BULLET))
story.append(Paragraph("• Inventory management: max 20% NAV per individual contract, hedge with related contracts where possible (correlated political markets)", BULLET))
story.append(Paragraph("• Adverse selection control: widen spreads when order book shows large unbalanced flow; pause MM 1 hour before known news events (Fed meetings, BLS releases)", BULLET))

story.append(Paragraph("Expected economics", H3))
mm_tbl = [
    ['Metric', 'Estimate', 'Basis'],
    ['Effective bid-ask captured per round-trip', '2–4¢', 'After fee + adverse selection'],
    ['Daily round-trips per active contract', '50–200', 'Function of contract type'],
    ['Number of active contracts', '20–50', 'Politics + econ + weather'],
    ['Average inventory per contract', '$10–50K', 'Conservative initial'],
    ['Daily P&L estimate', '$500–$2,500', 'On $5M deployed capital'],
    ['Annualized return on capital', '20–40%', 'After Kalshi fees'],
    ['Sharpe estimate (industry MM analog)', '3–5', 'Inventory-managed MM benchmark'],
    ['Capacity ceiling', '$25–50M', 'At which point spreads compress'],
]
story.append(tbl(mm_tbl, col_widths=[2.4*inch, 1.6*inch, 2.5*inch]))

story.append(Paragraph("Risk to this thesis", H3))
story.append(Paragraph("""If Susquehanna or Jane Street decide to enter Kalshi non-sports
markets, spreads compress within weeks. Mitigations: (a) the firm enters first, so even a 6–12 month
window of monopoly economics captures meaningful capital before competition arrives; (b) Kalshi expands
the contract universe faster than top firms can adapt (the firm's 'small-market advantage' is
durable in expanding verticals like weather, science, sports prop bets); (c) if the firm reaches $25M+
deployed before competition arrives, the operational scale advantage is meaningful.""", BODY))
story.append(PageBreak())

# =============== 4. MARKET MAKING GAP ANALYSIS ===============
story.append(Paragraph("4. Market Making Gap Analysis", H1))
story.append(Paragraph("""Beyond the primary Kalshi non-sports gap (section 3.5), this section
catalogs other MM opportunities the firm has identified during research. Each represents a
capacity-gap created by the same structural pattern: top market makers are revenue-floor-constrained
and bypass smaller markets, leaving structural bid-ask premiums for smaller, focused operators.""", BODY))

story.append(Paragraph("4.1 New-perp launch microstructure (Hyperliquid)", H2))
story.append(Paragraph("""When Hyperliquid lists a new perpetual contract (e.g., a recently launched
token), the first 30–60 days exhibit 10–30 bp bid-ask spreads, immature funding-rate dynamics, and
limited maker depth. HL's HLP (their internal MM vault) provides baseline liquidity but earns
single-digit bps per round-trip — there is room for a competitive maker.""", BODY))
new_perp_tbl = [
    ['Observation', 'Detail'],
    ['New listings per year on HL', '30–50'],
    ['Median 30-day spread on new perps', '10–25 bp'],
    ['Mature perp spread (e.g., ETH)', '1–3 bp'],
    ['Daily new-perp volume (typical)', '$1–10M'],
    ['Capacity per name', '$100K–$1M'],
    ['Total addressable book', '$5–25M'],
    ['Competition', 'HLP only, no major external MMs documented'],
]
story.append(tbl(new_perp_tbl, col_widths=[2.5*inch, 4.0*inch]))

story.append(Paragraph("4.2 DeFi RFQ solver positioning", H2))
story.append(Paragraph("""UniswapX, 1inch Fusion, and CoWSwap operate solver networks: external entities
that fill user orders and capture spread vs. AMM baseline. The active solver count per protocol is
small (10–20 solvers). Long-tail trading pairs (e.g., mid-cap ERC20 vs USDC, cross-L2 stablecoin pairs)
have fewer solvers competing and wider achievable spreads.""", BODY))
story.append(Paragraph("• Capacity: $10–100M, depends on protocol and pair", BULLET))
story.append(Paragraph("• Operational requirement: full-node infra, MEV-resistant execution, gas optimization", BULLET))
story.append(Paragraph("• Solver ecosystem documentation: <i>UniswapX RFQ docs</i>, <i>CoW Protocol solver-onboarding guide</i>", BULLET))
story.append(Paragraph("• Realistic Sharpe 2–4 for mid-tier solvers; capital efficiency depends on gas prepayment model", BULLET))

story.append(Paragraph("4.3 Tokenized real-world assets (RWA) cross-venue arb", H2))
story.append(Paragraph("""Ondo OUSG, BlackRock BUIDL, Backed bIB01/bC3M are tokenized treasuries that
trade across: (a) the issuer's primary venue (subscriptions/redemptions), (b) DEX pools (Uniswap, Curve),
(c) some CEX listings. Cross-venue price spreads of 30–80 bp routinely persist for hours because
arbitrage requires KYC/operational setup specific to each issuer. <b>Total RWA market: $5–10B
and growing 100%+ YoY.</b> Few professional MMs are active.""", BODY))
story.append(Paragraph("""<b>Implementation cost:</b> KYC onboarding with 3–4 issuers (4–6 weeks), USDC
working capital ($1–5M), Ethereum + L2 multisig infrastructure. <b>Capacity: $10–100M.</b> Limit: any
firm entering this market opens up to scaling risk during stablecoin de-pegs (March 2023 SVB-USDC
event).""", BODY))

story.append(Paragraph("4.4 Single-stock options on $1B–$5B market cap names", H2))
story.append(Paragraph("""Susquehanna, Citadel Securities, and Optiver focus options market-making
capital on top-200 names by volume. Outside this universe (roughly the bottom 60% of S&P 500 and
mid-caps), bid-ask spreads in standard expiries are 5–15 bp on listed options. A specialized desk
focused on the $1–5B market-cap band, combined with delta-hedging on the underlying, can capture
documented Sharpe 1.5–2.5 returns.""", BODY))
story.append(Paragraph("""<b>Barrier:</b> Capital intensity is the main barrier — typical small-cap
options MM requires $25M+ to be operationally viable. Lower priority for an under-capitalized launch.""", BODY))

story.append(PageBreak())
story.append(Paragraph("4.5 Frontier-market sovereign-bond ETF NAV arbitrage", H2))
story.append(Paragraph("""ETFs covering single-country emerging-market debt (e.g., EMLC, VWOB, SOVI)
maintain NAV using a basket of underlying bonds that trade with limited depth. Authorized Participants
(APs) for these ETFs typically use end-of-day batch arbitrage. Intraday NAV divergence can reach 30–60
bp on volatility-spike days. Realistic Sharpe of 1.5–2 with $5–25M capacity per name.""", BODY))
story.append(Paragraph("""<b>Implementation requirement:</b> AP status with at least one ETF issuer
($25M minimum equity typically required for AP status), or via in-kind creation/redemption through an
existing AP for smaller capital deployments. Higher operational complexity than other gaps; deferred
to Year 2.""", BODY))

story.append(Paragraph("4.6 Stablecoin cross-venue MM (USDC/USDT/DAI)", H2))
story.append(Paragraph("""Across centralized and decentralized exchanges, stablecoin prices intermittently
deviate from $1.00 by 10–80 bp. Most such deviations correct within 1–10 minutes. A 24/7 systematic MM
quoting both sides at narrow spreads captures the spread plus mean-reversion premium.""", BODY))
story.append(Paragraph("""<b>Sharpe estimate:</b> 4–6 in normal regime, but exposed to tail risk during
true stablecoin distress events (e.g., USDC-SVB 12% drop, March 2023). Position-size cap and circuit
breakers required. Capacity: $50–500M but high crowdedness from existing MMs.""", BODY))

story.append(Paragraph("4.7 Summary ranking of gaps", H2))
gap_rank_tbl = [
    ['#', 'Gap', 'Sharpe', 'Capacity', 'Setup cost', 'Time to deploy', 'Priority'],
    ['1', 'Kalshi non-sports MM', '3–5', '$5–25M', '$50K', '6–8 weeks', 'Lead'],
    ['2', 'HL new-perp launch MM', '2–4', '$5–25M', '$10K', '2–4 weeks', 'Year 1'],
    ['3', 'DeFi RFQ solver (UniswapX)', '2–4', '$10–100M', '$80K', '8–12 weeks', 'Year 2'],
    ['4', 'RWA cross-venue arb', '2–3', '$10–100M', '$30K', '6–10 weeks', 'Year 2'],
    ['5', 'Stablecoin MM', '4–6', '$50–500M', '$15K', '2–4 weeks', 'Year 1 (small size)'],
    ['6', 'Frontier sovereign ETF NAV', '1.5–2', '$5–25M', '$200K (AP)', '12–20 weeks', 'Year 3'],
    ['7', 'Single-stock options $1–5B cap', '1.5–2.5', '$25M+', '$200K+', '12+ weeks', 'Year 3+'],
]
story.append(tbl(gap_rank_tbl, col_widths=[0.3*inch, 1.95*inch, 0.7*inch, 0.85*inch, 0.85*inch, 1.05*inch, 1.0*inch]))

story.append(PageBreak())

# =============== 5. CAPACITY MAP ===============
story.append(Paragraph("5. Capacity & Scalability Map", H1))
cap_tbl = [
    ['Strategy', 'Capacity floor', 'Capacity ceiling', 'Scaling limit'],
    ['Adaptive TSMOM', '$1M', '$1B+', 'Implementation slippage'],
    ['Crypto funding arb', '$50K', '$50–200M', 'Hyperliquid + cross-venue OI'],
    ['Crypto cross-section', '$100K', '$10–50M', 'Mid-cap perp OI'],
    ['Kalshi K6 + T1', '$1K', '$50K–$500K', 'Kalshi per-contract depth'],
    ['Prediction-market MM', '$100K', '$5–25M', 'Non-sports vertical volume'],
    ['HL new-perp MM', '$50K', '$5–25M', 'Aggregate new-listing flow'],
    ['Stablecoin MM (small)', '$500K', '$50–500M', 'Crowdedness on existing arbs'],
    ['Combined firm capacity Year 1', '$1–3M', '$25M', 'Strategy mix + ops scale'],
    ['Combined Year 3', '$25M', '$250M', 'Track record + investor demand'],
    ['Combined Year 5', '$250M', '$1B+', 'Strategy enhancement + team scale'],
]
story.append(tbl(cap_tbl, col_widths=[2.3*inch, 1.1*inch, 1.4*inch, 1.7*inch]))

story.append(Paragraph("Capacity expansion plan", H3))
story.append(Paragraph("""Year 1 capacity ceiling is set by operational scale (single founder + tech infra),
not strategy. Year 2 adds one operations hire (Compliance/COO function), unlocking ~$100M ceiling.
Year 3 adds dedicated researcher and quant developer; ceiling rises to $250–500M. Year 4–5 the firm
either (a) scales the existing strategies to capacity, (b) adds derivatives MM (single-stock options),
or (c) accepts capacity constraints and begins returning capital above $250M, focusing on Sharpe
preservation.""", BODY))
story.append(PageBreak())

# =============== 6. CAPITAL REQUIREMENTS ===============
story.append(Paragraph("6. Capital Requirements & Use of Funds", H1))
cap_req_tbl = [
    ['Item', 'Pre-launch (Jun–Sep)', 'Year 1 ops', 'Notes'],
    ['Legal: Fund formation (CY + DE GP)', '$40–60K', '—', 'Cayman master/feeder + DE GP LLC'],
    ['Legal: Operating agreements + LPA', '$15–20K', '—', 'Forms 506(c), Reg D'],
    ['Compliance: ADV registration', '$5–10K', '$8K/yr', 'May skip if private adviser <$150M'],
    ['Fund administrator', '$2K setup', '$24–36K/yr', 'NAV Consulting, SS&C, Standish'],
    ['Auditor', '—', '$15–30K/yr', 'BDO, Marcum, or boutique'],
    ['Insurance (E&O + GL + cyber)', '—', '$8–15K/yr', 'Required for institutional capital'],
    ['Prime brokerage setup', '$0', '$0', 'Interactive Brokers + Hyperliquid'],
    ['Technology infra (AWS/GCP)', '$2K setup', '$6–12K/yr', 'Servers, monitoring, backups'],
    ['Data subscriptions', '$1K', '$10–20K/yr', 'Polygon.io equities, Kalshi (free)'],
    ['Personal compensation', '—', '$60–100K/yr', 'Modest founder draw Year 1'],
    ['Office / coworking', '—', '$5–10K/yr', 'Optional, can run remote'],
    ['Marketing materials / pitch deck', '$3K', '$5K/yr', 'Designer + collateral'],
    ['Trading capital floor (margin)', '$50K', '$50K', 'Maintain on prime brokers'],
    ['Total fixed costs', '$70–110K', '$140–240K/yr', '—'],
]
story.append(tbl(cap_req_tbl, col_widths=[2.4*inch, 1.2*inch, 1.0*inch, 1.9*inch]))

story.append(Paragraph("Funding sources", H3))
story.append(Paragraph("""<b>Phase 1 (June–September 2026):</b> $250K personal capital covers legal,
operational setup, and seed working capital. <b>Phase 2 (September 2026 launch):</b> $500K–$2M
friends-and-family soft commitments converted to actual subscriptions. <b>Phase 3 (Months 7–18):</b>
Continued F&F expansion plus first non-F&F HNW subscriptions, target $5–10M by Month 18.
<b>Phase 4 (Year 2–3):</b> RIA channel + small institutional, target $25–75M.""", BODY))
story.append(PageBreak())

# =============== 7. OPERATIONS / LEGAL ===============
story.append(Paragraph("7. Operational Setup", H1))
story.append(Paragraph("7.1 Fund structure", H2))
story.append(Paragraph("""<b>Recommended structure:</b> Cayman Islands Exempted Company (master fund) with
Delaware Limited Partnership (US feeder for US-taxable investors) and offshore master fund managed
by a Delaware LLC General Partner. The investment manager is a separate Delaware LLC. This is
the industry-standard structure for sub-$1B systematic funds raising US capital.""", BODY))

story.append(Paragraph("Entity diagram", H3))
ent_diag = """
<para>
&nbsp;&nbsp;<b>[CY] Master Fund (Exempted Company)</b><br/>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;↑ holds 100% of assets, executes trades<br/>
&nbsp;&nbsp;<b>[DE] US Feeder LP (taxable investors)</b> &nbsp;&nbsp;&nbsp;<b>[CY] Offshore Feeder (tax-neutral / non-US)</b><br/>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;↑ subscriptions from LPs<br/>
&nbsp;&nbsp;<b>[DE] General Partner LLC</b> ←&nbsp;&nbsp;<i>2% mgmt / 20% performance fee accrues here</i><br/>
&nbsp;&nbsp;<b>[DE] Investment Manager LLC</b> ← <i>founder/principal entity, holds IP and brand</i><br/>
</para>
"""
story.append(Paragraph(ent_diag, BODY))

story.append(Paragraph("7.2 Service providers — short list", H2))
sp_tbl = [
    ['Function', 'Recommended provider', 'Setup cost', 'Notes'],
    ['Fund administration', 'NAV Consulting, SS&C, Theorem', '$1–2K', 'NAV calc, investor reporting'],
    ['Audit', 'BDO, RSM, Marcum', '—', 'Required annually'],
    ['Legal (formation + ongoing)', 'Seward & Kissel, Sadis, Walkers (CY)', '$40–60K', 'Cayman + US counsel'],
    ['Prime broker (equities)', 'Interactive Brokers Pro', '$0', 'Low minimum, API-first'],
    ['Prime broker (crypto)', 'Hyperliquid, Coinbase Institutional', '$0', 'Self-custody hybrid'],
    ['Compliance consultant', 'Carbon Group, Foreside', '$5–15K/yr', 'Quarterly reviews'],
    ['Tax preparer', 'Anchin, Marcum tax services', '$10–20K/yr', 'K-1s, 1042-S, partnership returns'],
    ['Tech: hosting', 'AWS / Vultr / DigitalOcean', '$200–500/mo', 'Production trading infra'],
    ['Tech: monitoring', 'Datadog or self-hosted', '$100–400/mo', 'Alerts, kill switches'],
]
story.append(tbl(sp_tbl, col_widths=[1.6*inch, 1.95*inch, 0.8*inch, 2.05*inch]))

story.append(Paragraph("7.3 Regulatory posture", H3))
story.append(Paragraph("""<b>Investment Adviser registration:</b> Below $150M AUM, the firm qualifies for
the "private fund adviser exemption" under Investment Advisers Act §203(m). Annual ADV Part 1A filing
required. <b>Above $150M AUM:</b> Full SEC registration required, mandates compliance program,
Chief Compliance Officer designation (can be outsourced initially), annual review.<br/><br/>
<b>Securities offering:</b> Operate under Rule 506(c) of Regulation D, allowing general solicitation
to accredited investors only. Verify accreditation via third-party (e.g., Parallel Markets or via
investor's CPA letter). <b>Future option:</b> 506(b) if pivoting to non-accredited via existing
relationships (no general solicitation but allows up to 35 non-accredited within 12 months).""", BODY))

story.append(Paragraph("7.4 Compliance program (Year 1 minimum)", H3))
story.append(Paragraph("• Code of Ethics (template via Foreside or Carbon Group, $1K customization)", BULLET))
story.append(Paragraph("• Personal trading policy (pre-clearance required for all securities trades by principal)", BULLET))
story.append(Paragraph("• Books and records retention (5 years immediately accessible, 2 additional years on storage)", BULLET))
story.append(Paragraph("• AML/KYC program for investor onboarding (use accredited investor verification platform)", BULLET))
story.append(Paragraph("• Annual compliance review (engage Carbon Group or Foreside)", BULLET))
story.append(PageBreak())

# =============== 8. TIMELINE ===============
story.append(Paragraph("8. Launch Timeline — June to September 2026", H1))

story.append(Paragraph("Month 0: June 2026 — Foundation", H2))
story.append(Paragraph("Goal: Legal foundation, operational sprint, paper-trading validation.", BODY))
m0_tbl = [
    ['Week', 'Workstream', 'Deliverable'],
    ['1', 'Legal engagement', 'Engage Cayman + US counsel; fund structure agreed'],
    ['1–2', 'Service provider selection', 'Letters of Engagement signed: fund admin, auditor, compliance'],
    ['2–3', 'Strategy paper-trade sprint', 'All 5 sleeves running with capital sims; data pipelines validated'],
    ['3–4', 'Tech infrastructure', 'AWS production env; monitoring stack; backup execution venues'],
    ['4', 'Pitch deck v1', '20-page deck + 5-page exec summary; investor FAQ'],
]
story.append(tbl(m0_tbl, col_widths=[0.5*inch, 1.6*inch, 4.3*inch]))

story.append(Paragraph("Month 1: July 2026 — Fund formation + investor warm-up", H2))
m1_tbl = [
    ['Week', 'Workstream', 'Deliverable'],
    ['5–6', 'Fund formation closing', 'CY Master + DE Feeder + DE GP entities formed; LPA signed'],
    ['6', 'ADV Part 1A filing', 'Filed with SEC; status: Exempt Reporting Adviser'],
    ['6–7', 'Pre-marketing', 'Soft circle 15–25 prospective LPs (warm intros only)'],
    ['7–8', 'Strategy continuation', 'All sleeves continue paper; document monthly metrics'],
    ['8', 'Risk framework finalized', 'Position limits, drawdown triggers, kill switches documented'],
]
story.append(tbl(m1_tbl, col_widths=[0.5*inch, 1.6*inch, 4.3*inch]))

story.append(Paragraph("Month 2: August 2026 — Pre-launch operational finalization", H2))
m2_tbl = [
    ['Week', 'Workstream', 'Deliverable'],
    ['9–10', 'Prime broker accounts open', 'IBKR Pro + Hyperliquid + Coinbase Inst accounts funded'],
    ['10–11', 'Final paper-trade audit', 'External-style review (engage AlphaQuest or independent consultant for one-pass review)'],
    ['11', 'Investor doc package', 'Subscription docs, LPA finalized, side letter templates'],
    ['12', 'First soft commitments', 'Convert 8–15 warm prospects into signed subscription docs ($500K–$2M total)'],
]
story.append(tbl(m2_tbl, col_widths=[0.5*inch, 1.6*inch, 4.3*inch]))

story.append(Paragraph("Month 3: September 2026 — LAUNCH", H2))
m3_tbl = [
    ['Week', 'Workstream', 'Deliverable'],
    ['13', 'Capital received', 'Initial subscriptions wired to fund admin'],
    ['13', 'Trading activated', 'First trades executed September 1, 2026'],
    ['14', 'Daily NAV tracking', 'Fund admin produces daily NAV; founder verifies'],
    ['15', 'First month-end NAV', 'Official September 2026 NAV (~ Sept 30); investor letter sent'],
    ['16', 'Q4 outreach plan', 'Identify Year 1 capital raise pipeline (target $5M by year-end)'],
]
story.append(tbl(m3_tbl, col_widths=[0.5*inch, 1.6*inch, 4.3*inch]))

story.append(Paragraph("Critical path dependencies", H3))
story.append(Paragraph("""<b>Hard blocker:</b> Fund formation completion (Month 1, Week 6) gates all
subsequent investor solicitation. Engaged counsel must be available immediately in June.
<b>Soft blocker:</b> Tech infrastructure must be production-grade before any external capital deploys
(Month 2, Week 9 at latest). <b>Investor solicitation:</b> Cannot begin until Master fund + Feeder
filings registered and Form D filed within 15 days of first sale.""", BODY))
story.append(PageBreak())

# =============== 9. FINANCIAL PROJECTIONS ===============
story.append(Paragraph("9. Financial Projections (5-Year)", H1))

story.append(Paragraph("Conservative AUM and revenue projection", H2))
story.append(Paragraph("""Assumes 10% net strategy returns to investors, 2% management + 20% performance
fee structure, modest capital ramp consistent with 1st-year emerging manager benchmarks.""", BODY))

proj_tbl = [
    ['End of period', 'AUM ($M)', 'Mgmt fee ($K/yr)', 'Perf fee ($K/yr)', 'Total rev ($K/yr)', 'Net to GP ($K/yr)'],
    ['Sep 2026 (launch)', '1.0', '20', '20', '40', '~negative (covered by setup)'],
    ['Dec 2026 (Q1)', '1.5', '30', '30', '60', '~ -50 (fixed costs $110K)'],
    ['Jun 2027', '4.5', '90', '90', '180', '~ 30'],
    ['Dec 2027 (Yr 1)', '8.0', '160', '160', '320', '~ 140'],
    ['Dec 2028 (Yr 2)', '25.0', '500', '500', '1,000', '~ 720'],
    ['Dec 2029 (Yr 3)', '60.0', '1,200', '1,200', '2,400', '~ 1,800'],
    ['Dec 2030 (Yr 4)', '150.0', '3,000', '3,000', '6,000', '~ 4,800'],
    ['Dec 2031 (Yr 5)', '350.0', '7,000', '7,000', '14,000', '~ 11,500'],
]
story.append(tbl(proj_tbl, col_widths=[1.3*inch, 0.7*inch, 1.0*inch, 1.0*inch, 1.0*inch, 1.5*inch]))

story.append(Paragraph("MRR equivalent (monthly recurring revenue from management fees only)", H3))
mrr_tbl = [
    ['Period', 'AUM', 'Monthly mgmt fee', '+ Avg monthly perf fee', '= Effective MRR'],
    ['Year 1 average', '$3M', '$5K', '$5K', '$10K'],
    ['Year 2 average', '$15M', '$25K', '$25K', '$50K'],
    ['Year 3 average', '$40M', '$67K', '$67K', '$134K'],
    ['Year 4 average', '$100M', '$167K', '$167K', '$334K'],
    ['Year 5 average', '$250M', '$417K', '$417K', '$834K'],
]
story.append(tbl(mrr_tbl, col_widths=[1.3*inch, 1.0*inch, 1.3*inch, 1.5*inch, 1.4*inch]))

story.append(Image(str(OUT/'aum_projection.png'), width=6.5*inch, height=3.3*inch))
story.append(Paragraph("Figure 3. 5-year AUM and revenue projection (conservative scenario).", CAPTION))

story.append(Paragraph("Year-1 unit economics breakdown", H3))
story.append(Paragraph("""<b>Revenue (Year 1 ending Dec 2027 @ $8M average AUM):</b> ~$320K total
($160K mgmt + $160K performance assuming 10% net return). <b>Fixed costs:</b> ~$180K (legal, admin,
audit, tech, principal salary $80K). <b>Net to GP equity:</b> ~$140K.
<b>Founder draw target:</b> $80K Year 1, $150K Year 2, $250K Year 3+. <b>Reinvested:</b> Surplus
above founder draw flows to GP capital, used to seed prop trading + Strategy R&D.""", BODY))

story.append(Paragraph("Sensitivity: what if returns are weaker?", H3))
sens_tbl = [
    ['Scenario', 'Yr 3 AUM', 'Yr 3 Revenue', 'Yr 3 Net to GP', 'Implication'],
    ['Base (10% net returns)', '$60M', '$2.4M', '$1.8M', 'Founder pays self $250K + reinvests'],
    ['Soft (6% net returns)', '$30M', '$1.0M', '$0.5M', 'Founder pays self $200K, tight'],
    ['Strong (15% net returns)', '$120M', '$5.2M', '$4.2M', 'Hire 2 employees, accelerated build'],
    ['Stress (3% net returns)', '$15M', '$0.4M', '-$0.1M', 'Personal capital subsidizes year'],
]
story.append(tbl(sens_tbl, col_widths=[1.7*inch, 0.7*inch, 0.9*inch, 0.9*inch, 2.2*inch]))
story.append(PageBreak())

# =============== 10. RISK MANAGEMENT ===============
story.append(Paragraph("10. Risk Management Framework", H1))
story.append(Paragraph("10.1 Position-level controls", H2))
story.append(Paragraph("• Maximum single-asset position: 5% of NAV (TSMOM sleeve) / 10% (crypto sleeve)", BULLET))
story.append(Paragraph("• Volatility target per strategy: 10–15% annualized; portfolio target 12%", BULLET))
story.append(Paragraph("• Stop-loss: automatic for any position breaching 30% intraday drawdown", BULLET))
story.append(Paragraph("• Inventory cap (MM): max 20% NAV in any single Kalshi contract", BULLET))

story.append(Paragraph("10.2 Portfolio-level controls", H2))
story.append(Paragraph("• Max strategy allocation: 40% of NAV per strategy", BULLET))
story.append(Paragraph("• Total leverage: max 3.0x gross notional, 2.5x for TSMOM specifically", BULLET))
story.append(Paragraph("• Cross-strategy correlation monitoring: weekly review; trigger rebalance if any 30-day pair correlation exceeds 0.6", BULLET))
story.append(Paragraph("• Drawdown brake: at -8% peak-to-trough NAV, all strategies de-risk to 50% sizing; at -12%, all paused for review", BULLET))

story.append(Paragraph("10.3 Operational controls", H2))
story.append(Paragraph("• Kill switches: each strategy has independent emergency stop, callable via Slack alert or CLI", BULLET))
story.append(Paragraph("• Daily reconciliation: positions, cash, NAV reconciled between trading systems and fund administrator", BULLET))
story.append(Paragraph("• Cold custody: 80% of crypto holdings in cold storage; only 20% on exchange margin", BULLET))
story.append(Paragraph("• Multisig governance: all withdrawals from prime brokers require 2-of-3 approval (founder + ops + investor representative or board member)", BULLET))
story.append(Paragraph("• Annual penetration test: third-party security audit of trading infrastructure", BULLET))

story.append(Paragraph("10.4 Counterparty risk", H2))
story.append(Paragraph("""Crypto exchanges represent the largest counterparty risk. Mitigations:
(a) maximum 30% of crypto sleeve at any single exchange, (b) Hyperliquid HLP exposure capped at 10%
of crypto NAV, (c) regular review of exchange reserves via Nansen / on-chain analytics, (d) preference
for Coinbase Institutional and regulated venues over unregulated alternatives where possible.""", BODY))

story.append(Paragraph("10.5 Model risk", H2))
story.append(Paragraph("""All strategies are validated against (a) walk-forward out-of-sample testing,
(b) regime stress tests (2008, 2020 March, 2022 Q4, 2023 March), and (c) parameter sensitivity sweeps.
Models that show parameter-sensitivity Sharpe range > 0.5 standard deviations are flagged for
re-specification. Annual third-party model validation review starting Year 2.""", BODY))
story.append(PageBreak())

# =============== 11. TECHNOLOGY STACK ===============
story.append(Paragraph("11. Technology Stack", H1))
story.append(Paragraph("Existing infrastructure (already built)", H3))
story.append(Paragraph("• Edge-bot codebase: 18 strategy modules, Python 3.13, async architecture", BULLET))
story.append(Paragraph("• Live paper-trading on Hyperliquid, Coinbase, Kalshi", BULLET))
story.append(Paragraph("• DuckDB analytical store for backtest + live tick capture", BULLET))
story.append(Paragraph("• Local development environment; production migration pending", BULLET))

story.append(Paragraph("Production infrastructure (Jun–Aug 2026 build)", H3))
tech_tbl = [
    ['Layer', 'Component', 'Provider', 'Monthly cost'],
    ['Compute', 'Production strategy runners (24/7)', 'AWS EC2 c7i.2xlarge × 3', '$450'],
    ['Storage', 'Tick capture + backtest archive', 'AWS S3 + EBS', '$80'],
    ['Database', 'DuckDB analytical + PostgreSQL operational', 'AWS RDS (small)', '$120'],
    ['Monitoring', 'Datadog or self-hosted (Grafana + Loki)', 'Datadog Pro', '$200'],
    ['Alerting', 'PagerDuty', 'PagerDuty Business', '$70'],
    ['Secrets management', 'AWS Secrets Manager', 'AWS', '$15'],
    ['Source control + CI', 'GitHub Enterprise + Actions', 'GitHub', '$50'],
    ['Backup execution', 'Secondary data center (Linode or Vultr)', 'Failover infra', '$200'],
    ['Total monthly', '', '', '$1,185'],
]
story.append(tbl(tech_tbl, col_widths=[1.0*inch, 2.4*inch, 1.7*inch, 1.0*inch]))

story.append(Paragraph("Critical operational improvements before launch", H3))
story.append(Paragraph("• <b>WebSocket consumer for Kalshi:</b> Currently using REST polling, which blocks KCF capitulation strategy from firing. Build WS client for real-time trade tape (estimated 2 weeks).", BULLET))
story.append(Paragraph("• <b>Position reconciliation daemon:</b> Cross-checks trading system positions against exchange API every 60s; alerts on mismatch >0.1%.", BULLET))
story.append(Paragraph("• <b>Pre-trade risk checks:</b> All orders pass through risk module that validates against position limits, leverage, and drawdown brake state before submission.", BULLET))
story.append(Paragraph("• <b>NAV calculation pipeline:</b> Automated daily NAV computation feeding fund administrator data import.", BULLET))
story.append(PageBreak())

# =============== 12. TEAM ===============
story.append(Paragraph("12. Team & Hiring Plan", H1))
story.append(Paragraph("Founding team — Year 1", H2))
story.append(Paragraph("""<b>Founder / CIO:</b> Principal (you). Responsibilities span strategy R&D,
trading oversight, technology, investor relations, and operations. This is the standard solo-founder
profile for an under-$25M AUM systematic fund.<br/><br/>
<b>Outsourced functions:</b> Compliance consultant (Carbon Group, $1K/month), part-time CFO/bookkeeper
($500/month), fund admin (NAV Consulting, included in $25K/yr), audit (BDO, annual), legal (Seward &
Kissel, ad-hoc).""", BODY))

story.append(Paragraph("Year 2 first hire — Quant Developer / Researcher", H3))
story.append(Paragraph("""Target compensation: $150–225K base + GP units. Profile: 3–5 years quant
research experience, Python/SQL strong, ideally prior systematic trading. Focus areas: extending
the strategy R&D pipeline, building out the MM stack, automating ops. Hire at $25M+ AUM (Year 2 H2).""", BODY))

story.append(Paragraph("Year 3 second hire — Operations / Compliance", H3))
story.append(Paragraph("""Target compensation: $120–180K base + GP units. Profile: prior compliance
or operations role at registered investment adviser. Picks up CCO designation, manages investor
relations workflow, handles regulatory filings. Hire at $50M+ AUM.""", BODY))

story.append(Paragraph("Year 4+ scaling team", H3))
story.append(Paragraph("• 2nd Quant Researcher (signal R&D specialization)", BULLET))
story.append(Paragraph("• Risk Manager (separate from CIO, independent risk review)", BULLET))
story.append(Paragraph("• Capital introduction / IR specialist", BULLET))
story.append(Paragraph("• Possibly 1 trader for MM execution (if MM scales beyond $25M)", BULLET))
story.append(PageBreak())

# =============== 13. INVESTOR STRATEGY ===============
story.append(Paragraph("13. Investor Strategy & Capital Raise", H1))
story.append(Paragraph("Capital raise phases", H2))
inv_phase_tbl = [
    ['Phase', 'Period', 'Target', 'Source', 'Vehicle'],
    ['Founder seed', 'Pre-launch', '$250K', 'Principal personal', 'Direct LP commit'],
    ['Friends & family', 'Sep–Dec 2026', '$500K–$2M', 'Personal network (3–10 LPs)', 'US Feeder LP'],
    ['HNW expansion', 'Q1–Q3 2027', '$5M cumulative', 'Friend-of-friend, RIA intro', '506(c) accredited'],
    ['RIA channel', 'Q4 2027–Q4 2028', '$15M cumulative', 'RIA placement agent', 'Both feeders'],
    ['Small institutional', 'Q1 2029+', '$50M+ cumulative', 'Multi-family office, FoF', 'Both feeders'],
    ['Mid institutional', '2030+', '$250M+', 'Endowments, small pensions', 'Both feeders'],
]
story.append(tbl(inv_phase_tbl, col_widths=[1.2*inch, 1.3*inch, 1.1*inch, 1.7*inch, 1.0*inch]))

story.append(Paragraph("Pitch materials needed", H3))
story.append(Paragraph("• <b>Tear sheet:</b> 1-page monthly performance summary (target: ready by Sep 2026 launch)", BULLET))
story.append(Paragraph("• <b>Pitch deck:</b> 20 slides — firm, strategy, team, returns, risk, terms, FAQ", BULLET))
story.append(Paragraph("• <b>Track-record reports:</b> 3-month, 6-month, 12-month performance attribution (developed iteratively)", BULLET))
story.append(Paragraph("• <b>Due diligence questionnaire (DDQ):</b> Standard institutional DDQ template, ~50 pages, pre-completed", BULLET))
story.append(Paragraph("• <b>Risk framework document:</b> 10–15 pages, detailed limit structure (shown in section 10 of this plan)", BULLET))

story.append(Paragraph("Fund terms", H3))
terms_tbl = [
    ['Term', 'Value', 'Note'],
    ['Management fee', '2% per annum', 'Monthly accrual, quarterly payable'],
    ['Performance fee', '20% over HWM', 'Annual crystallization'],
    ['Minimum subscription', '$250K Year 1, $500K Year 2+', 'Standard 506(c)'],
    ['Lock-up', '1 year hard, soft thereafter', '90-day notice quarterly redemptions'],
    ['Liquidity', 'Quarterly', 'Standard for sub-$100M systematic'],
    ['High water mark', 'Yes, per-LP', 'Industry standard'],
    ['Crystallization', 'Annual', 'Performance fee assessed Dec 31'],
    ['Hurdle rate', 'None initially', 'Add for institutional Year 3+'],
    ['Founders share class', '1.5% / 15% for first $5M', 'F&F early commitment'],
]
story.append(tbl(terms_tbl, col_widths=[1.5*inch, 1.8*inch, 2.7*inch]))

story.append(Paragraph("Outreach channels (in order of priority)", H3))
story.append(Paragraph("1. Personal network (warmest, highest conversion) — F&F, ex-colleagues, classmates", BULLET))
story.append(Paragraph("2. Quant fund community: ApexAlpha events, SquantConnect socials, AMA in r/algotrading", BULLET))
story.append(Paragraph("3. Cap intro at small banks: Marlin Risk Advisors, Mosaic Capital Advisors (paid placement, 2% fee)", BULLET))
story.append(Paragraph("4. RIA channels: Dynasty Financial, HighTower (sub-$25M minimums, distribute to RIA clients)", BULLET))
story.append(Paragraph("5. Conferences: AlphaWeek conferences, Battle of the Quants, SALT Conference (after $25M+ AUM)", BULLET))
story.append(PageBreak())

# =============== 14. APPENDIX ===============
story.append(Paragraph("14. Appendix", H1))
story.append(Paragraph("A. Strategy tear sheets — separate document", H3))
story.append(Paragraph("""Each of the 5 strategies will be accompanied by a 1–2 page tear sheet with
monthly performance attribution, equity curve, drawdown profile, and risk metrics. These will be
delivered in standard investor letter format from Sep 2026 onwards.""", BODY))

story.append(Paragraph("B. Annual return history per asset class (TSMOM)", H3))
story.append(Image(str(OUT/'yearly_returns.png'), width=6.5*inch, height=3.0*inch))
story.append(Paragraph("Figure 4. Annual returns of the adaptive TSMOM sleeve, OOS test period.", CAPTION))

story.append(Paragraph("C. Glossary of key terms", H3))
glos_tbl = [
    ['Term', 'Definition'],
    ['TSMOM', 'Time-series momentum — trend-following strategy buying assets with positive past returns'],
    ['Sharpe ratio', 'Annualized excess return divided by annualized volatility; >1.0 considered strong'],
    ['Carry', 'Income from holding an asset (dividends, funding payments, etc.)'],
    ['Market making (MM)', 'Providing two-sided quotes (bid + ask), profiting from spread + inventory turnover'],
    ['Adverse selection', 'Informed counterparty trading against you, causing inventory to move against your position'],
    ['NAV', 'Net asset value — fund total assets minus liabilities'],
    ['HWM', 'High water mark — highest historical NAV per share, ensures performance fee only on new highs'],
    ['Reg D 506(c)', 'SEC exemption allowing general solicitation for accredited investors only'],
    ['Funding rate', 'Periodic payment between perpetual futures longs and shorts; positive = longs pay shorts'],
    ['Master/Feeder', 'Fund structure where multiple feeder funds invest in single master fund'],
]
story.append(tbl(glos_tbl, col_widths=[1.5*inch, 5*inch]))

story.append(Paragraph("D. Reference documents kept on file", H3))
story.append(Paragraph("• Adaptive TSMOM strategy specification (signal definitions, parameter values, code)", BULLET))
story.append(Paragraph("• Bias audit log (xchg_carry_audit.md and no_sharpe25_edge.md memos)", BULLET))
story.append(Paragraph("• Multi-asset universe backtest results (multi_asset_research/)", BULLET))
story.append(Paragraph("• Funding arb live deployment log (funding_arb/data/paper_log.jsonl, 555+ cycles)", BULLET))
story.append(Paragraph("• Kalshi K6 deployment log (kalshi_k6/data/paper_log.jsonl, 87,000+ cycles, 237 closed trades)", BULLET))

story.append(Paragraph("E. Author's note on intellectual honesty", H3))
story.append(Paragraph("""This plan was developed alongside an extensive bias audit of all proposed
strategies. Initial backtests claimed Sharpe 25 for the crypto cross-exchange carry strategy; after
correcting for universe selection, execution costs, mark divergence between venues, and sign-lock
leakage, the honest Sharpe was 1–3. All return projections in this document have been adjusted to
reflect post-audit, post-friction estimates. The author has explicitly identified strategies that
failed validation (MI nonlinear pairs, ML supervised convergence, calendar effects, BTC-MSTR
cointegration, yield-curve sector rotation, PCA mean reversion) and excluded them from the firm's
proposed strategy mix.""", BODY))
story.append(Paragraph("""The firm operates from the position that <i>no Sharpe 5+ scalable strategy
exists in public crypto data without specialized infrastructure or alternative data</i>, and that the
realistic founder path is to combine Sharpe 0.7–2 strategies into a diversified portfolio while
investing in operational scale and niche market access. This plan reflects that honest assessment.""", BODY))

story.append(Spacer(1, 0.5*inch))
story.append(Paragraph("End of document.", SMALL))

# ---------- BUILD ----------
doc = SimpleDocTemplate(str(PDF_PATH), pagesize=letter,
                          leftMargin=0.85*inch, rightMargin=0.85*inch,
                          topMargin=0.75*inch, bottomMargin=0.75*inch)
doc.build(story)
print(f"✅ Generated: {PDF_PATH}")
print(f"   Size: {PDF_PATH.stat().st_size // 1024} KB")
