"""
Generate MODEL_EXPLAINED.pdf — a full, honest technical write-up of the bot.

    .venv/bin/python -m topstep_bot.build_doc
"""
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (ListFlowable, ListItem, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = "topstep_bot/MODEL_EXPLAINED.pdf"

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=16, spaceBefore=14, spaceAfter=6,
                    textColor=colors.HexColor("#1a3c5e"))
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4,
                    textColor=colors.HexColor("#2e6094"))
BODY = ParagraphStyle("BODY", parent=ss["BodyText"], fontSize=10, leading=14, alignment=TA_LEFT,
                      spaceAfter=6)
MONO = ParagraphStyle("MONO", parent=ss["Code"], fontSize=8.5, leading=11,
                      backColor=colors.HexColor("#f2f4f7"), borderPadding=4)
TITLE = ParagraphStyle("TITLE", parent=ss["Title"], fontSize=22, textColor=colors.HexColor("#1a3c5e"))
NOTE = ParagraphStyle("NOTE", parent=BODY, backColor=colors.HexColor("#fff5e6"), borderPadding=6,
                      borderColor=colors.HexColor("#e0a23c"), borderWidth=0.5)

E = []


def h1(t): E.append(Paragraph(t, H1))
def h2(t): E.append(Paragraph(t, H2))
def p(t): E.append(Paragraph(t, BODY))
def note(t): E.append(Paragraph("<b>Honest note:</b> " + t, NOTE)); E.append(Spacer(1, 4))
def mono(t): E.append(Paragraph(t.replace(" ", "&nbsp;").replace("\n", "<br/>"), MONO)); E.append(Spacer(1, 6))
def bullets(items):
    E.append(ListFlowable([ListItem(Paragraph(i, BODY), leftIndent=10) for i in items],
                          bulletType="bullet", start="square"))
    E.append(Spacer(1, 4))


def table(data, col_widths=None):
    t = Table(data, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3c5e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    E.append(t); E.append(Spacer(1, 8))


# ---------------------------------------------------------------- content
E.append(Paragraph("TopStep ICT + Microstructure Trading Bot", TITLE))
E.append(Spacer(1, 4))
E.append(Paragraph("Technical Documentation &amp; Honest Findings", H2))
E.append(Paragraph("Futures bot for TopStep via the ProjectX/TopstepX API — ICT signal engine, "
                   "deterministic risk management, ML position sizing, deep-learning research "
                   "pipeline, and live order-flow capture.", BODY))
E.append(Spacer(1, 6))

h1("1. Executive summary")
p("This system trades CME index/crypto micro futures (MNQ, MES, MBT) on a TopStep funded/eval "
  "account. It is built so that every trading idea is <b>validated before it risks money</b>: "
  "signals are rule-based and transparent, the risk engine caps downside to the eval fee, and an "
  "ML layer scales position size by a model-estimated win probability — but only a model that has "
  "passed out-of-sample (walk-forward) validation is ever allowed to size live.")
note("The headline research result so far is that the ICT bar-pattern strategy has <b>no "
     "out-of-sample directional edge</b> (win rates land on the random-walk baseline). The "
     "system correctly refused to deploy it. Current work tests whether true order-flow / "
     "microstructure data carries an edge that 1-minute bars cannot. No profitable model has "
     "been shipped; this document describes the machinery and the evidence, not a proven money "
     "printer.")

h1("2. System architecture")
p("The live loop, identical in paper (sim) and live mode — only the broker differs:")
mono("feed (quotes/trades/DOM)  ->  1-min bars + tape capture\n"
     "  ->  ICT signal engine  ->  setup?\n"
     "  ->  gates (R:R, killzone, flat, risk)\n"
     "  ->  ML confidence  ->  size = risk_cap x confidence_scale\n"
     "  ->  bracketed order (server-side OCO stop+target)\n"
     "  ->  manage: trailing-DD floor watch, flatten before close\n"
     "  ->  learn: label each trade win/loss -> retrain")
bullets([
    "<b>Broker layer</b> — abstract interface; <i>ProjectXBroker</i> (live REST + SignalR) and "
    "<i>SimBroker</i> (paper fills against the live feed) are interchangeable.",
    "<b>Data layer</b> — 1-min bar aggregation, multi-contract history backfill, and DuckDB "
    "capture of bars + the tape (every trade with aggressor side) + book snapshots.",
    "<b>Signal engine</b> — ICT primitives fused into risk-defined setups.",
    "<b>Risk engine</b> — models TopStep rules; the hard ceiling on size.",
    "<b>ML stack</b> — confidence/sizing model, deep research models, walk-forward validation.",
    "<b>Discord control</b> — status, pause/resume, flatten, manual-approve, live alerts."])

h1("3. Data layer")
bullets([
    "<b>Live feed:</b> ProjectX SignalR hubs stream quotes, the trade tape (with buy/sell "
    "aggressor), and DOM. Auth is an API-key login (24h JWT).",
    "<b>History:</b> /History/retrieveBars gives OHLC only; we stitch several contract months "
    "into ~2-3 months of 1-min bars per instrument for backtesting.",
    "<b>Capture-forward:</b> because no tick history is sold to us, the bot records the live tape "
    "and book into DuckDB, building the microstructure dataset over time.",
    "<b>Settlement/labels:</b> trades are walked forward bar-by-bar (stop checked before target = "
    "conservative) and booked net of the round-turn fee."])

h1("4. ICT signal engine")
p("ICT concepts are defined <b>operationally</b> (testable, not discretionary):")
bullets([
    "<b>Market structure:</b> fractal swing highs/lows; BOS (break of structure = trend "
    "continuation) and CHoCH (change of character = reversal); displacement = an impulsive bar "
    "whose body dwarfs ATR.",
    "<b>Fair Value Gaps (FVG):</b> 3-candle price imbalances; tracked until mitigated.",
    "<b>Order Blocks:</b> the last opposing candle before a displacement that breaks structure.",
    "<b>Liquidity pools &amp; sweeps:</b> clustered equal highs/lows where stops rest; a sweep "
    "is a wick through the pool that closes back inside (stop-grab + rejection).",
    "<b>Sessions / killzones:</b> Asian range, London &amp; NY killzones (ET); entries only "
    "inside killzones."])
h2("The two setup archetypes")
bullets([
    "<b>Reversal</b> ('price reverts to a point'): a liquidity sweep + rejection, confirmed by a "
    "CHoCH or displacement; stop beyond the swept wick, target the next opposing liquidity pool.",
    "<b>Continuation</b> ('momentum / bull-bear switch'): an established trend that printed a BOS, "
    "entered on a retrace into an unmitigated FVG/order block; stop beyond the zone, target the "
    "next draw on liquidity."])
h2("How stop and target are chosen")
p("<b>Stop</b> = the structural invalidation level (the swept wick, or the far edge of the "
  "FVG/order block) plus a 0.25*ATR buffer — if price trades there, the idea is wrong. "
  "<b>Target</b> = the nearest opposing liquidity pool, optionally floored to a minimum "
  "reward:risk multiple. R:R = (target-entry)/(entry-stop).")
note("R:R is a <b>planned, geometric</b> ratio — it does not include win probability or fees. "
     "A high win rate at small targets can still be net-negative once fees are paid. This is "
     "exactly what the data showed.")

h1("5. Risk management (the actual edge mechanism)")
p("Most funded accounts fail on the drawdown rules, not on signal. The risk engine models "
  "TopStep's End-of-Day <b>trailing</b> max drawdown, the daily-loss lock, max contracts, a "
  "loss-streak halt, and a give-back lock — and enforces them on <b>real-time intraday equity</b>, "
  "flattening the instant equity touches the trailing floor.")
table([["Account", "Profit target", "Trailing DD", "Daily loss", "Max contracts"],
       ["50K", "$3,000", "$2,000", "$1,000", "5"],
       ["100K", "$6,000", "$3,000", "$2,000", "10"],
       ["150K", "$9,000", "$4,500", "$3,000", "15"]],
      [1.0 * inch, 1.3 * inch, 1.1 * inch, 1.0 * inch, 1.3 * inch])
p("<b>Sizing = deterministic cap x ML confidence.</b> The risk engine computes the hard ceiling "
  "(contracts such that a full stop ~= per-trade risk, also bounded by distance-to-floor and the "
  "account max). The ML confidence (0-1) can only scale this <b>down</b>. Confidence never "
  "increases risk past the ceiling — that is the safety contract. Sizing amplifies an edge; it "
  "cannot create one (N contracts of a negative-EV trade lose N times faster).")

h1("6. The AI / ML stack")
bullets([
    "<b>Confidence model</b> — outputs P(win) for a setup. Cold start = a transparent hand-weighted "
    "logistic <i>prior</i> (not learned alpha); upgrades to a trained scikit-learn logistic once "
    "&gt;=200 labeled trades exist.",
    "<b>Deep model</b> — a 1D-CNN over the recent price-pattern window fused with the engineered "
    "feature vector (PyTorch, MPS-accelerated), heavily regularized for the modest dataset.",
    "<b>Directional model</b> — drops the ICT rules; predicts triple-barrier direction from the "
    "price window + microstructure features, trading only when confident.",
    "<b>Walk-forward validation</b> — train on the past, test on data never seen, roll forward; "
    "pool out-of-sample trades and bootstrap a confidence interval on mean PnL. A model ships only "
    "if OOS expectancy &gt; 0 with the CI above zero. This is the line that separates a real edge "
    "from a curve-fit."])

h1("7. Results so far — the honest verdicts")
p("Three independent, rigorous out-of-sample tests, all on ~143k bars / months of data:")
table([["Test", "OOS result", "Verdict"],
       ["ICT setups + CNN selector", "win 47%->66%, but -$2.41/trade (CI below 0)", "no edge"],
       ["Timeframe x R-multiple sweep (6 configs)", "every config -EV; win rate = random baseline", "no edge"],
       ["Pure-ML direction (price only)", "OOS accuracy ~44-49.5% (coin flip)", "no edge"]],
      [2.4 * inch, 3.0 * inch, 0.9 * inch])
p("Interpretation: the CNN <b>works</b> as a selector (it lifted win rate to 66% and scored "
  "AUC ~0.74), but the underlying trades don't clear fees, and at fixed R-multiples the win rates "
  "fall exactly on the driftless random-walk baseline (~1/(1+R)). Conclusion: <b>1-minute bar "
  "patterns carry no directional information</b> on this data — consistent with market efficiency "
  "at this horizon and with prior research in this repo.")

h1("8. The microstructure pivot (current work)")
p("Profitable discretionary day traders read the <b>order book and the tape</b> — order-flow "
  "imbalance, absorption, sweeps, book pressure — none of which survive compression into a 1-min "
  "OHLC bar. So the tests above ruled out bar-pattern edge, NOT microstructure edge. The bot now "
  "captures the real data:")
bullets([
    "<b>Tape capture</b> — every trade with its aggressor side, into DuckDB (working; ~tens of "
    "thousands of trades/session during RTH).",
    "<b>Order-flow features</b> — signed-volume imbalance, VPIN-style toxicity, aggressor-run "
    "persistence, large-print ratio, trade intensity, book pressure / micro-price.",
    "<b>Model</b> — a sequence CNN over micro-buckets of the tape + these features, predicting "
    "short-horizon triple-barrier direction, judged by the same walk-forward gate.",
    "<b>Book/DOM capture</b> — subscribed but not yet populating (entitlement/parse follow-up)."])
note("A live parsing bug had been silently discarding the entire feed (quotes key the contract as "
     "'contract'; trades arrive as a list) — fixed, so capture now works. The microstructure model "
     "has not yet been validated; it will be tested OOS once enough tape accumulates, and shipped "
     "only if it clears the same gate. It may also turn out to be ~random — that is an honest "
     "possible outcome.")

h1("9. How to run &amp; check progress")
mono("# paper-trade the live feed + capture order flow (zero account risk)\n"
     "python -m topstep_bot.run --mode sim --no-discord\n\n"
     "# check live progress (reads a heartbeat, no DB lock)\n"
     "python -m topstep_bot.status\n\n"
     "# backtest the ICT strategy on captured/stitched history\n"
     "python -m topstep_bot.backtest --instrument MNQ\n\n"
     "# build + walk-forward the microstructure model when enough tape exists\n"
     "python -m topstep_bot.ml.microstructure --instrument MNQ\n\n"
     "# continuous deep-learning loop: re-evaluate as data grows, ship only if +EV OOS\n"
     "python -m topstep_bot.ml.deep_train --loop --refresh-days 10")

h1("10. Honest limitations &amp; next steps")
bullets([
    "No profitable model has been validated or shipped. The bot trades on a conservative prior and "
    "is intended for sim until a model clears OOS.",
    "Modest data (~2-3 months bars; tick data only from now). Deep learning is data-hungry; results "
    "are small-sample until more accumulates.",
    "Live SignalR payloads, OCO bracket behavior, and DOM capture need confirmation on a live "
    "account before real size.",
    "Sim fills are optimistic (touch fills, 1-tick slippage); real fills will be worse.",
    "Next: accumulate RTH tape, run the microstructure walk-forward, and either wire it live (if "
    "+EV OOS) or report honestly that intraday edge is not capturable at retail latency."])
E.append(Spacer(1, 10))
p("<i>This document reflects the system and evidence as built. Negative results are reported as "
  "negative; nothing here is presented as a guaranteed edge.</i>")


def main():
    doc = SimpleDocTemplate(OUT, pagesize=LETTER, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            title="TopStep ICT + Microstructure Bot — Technical Documentation")
    doc.build(E)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
