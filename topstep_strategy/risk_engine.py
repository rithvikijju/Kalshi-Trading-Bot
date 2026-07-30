"""
TopStep funded-account RISK ENGINE — the part that actually determines whether you keep
a funded account. (Most funded traders don't fail on signal; they fail on the trailing
drawdown / daily-loss rules.)

Models TopStep's real rules:
  * End-of-Day TRAILING max drawdown: the loss floor starts at (start - maxDD) and trails
    UP using each day's END-OF-DAY balance high-water; it LOCKS at the starting balance
    once your EOD high is up by maxDD. Intraday equity hitting the floor = account blown.
  * Daily Loss Limit (soft): if intraday day-PnL <= -dailyLimit, you're auto-flattened and
    locked out for that session (not a violation, but no more trading that day).
  * Max position size (contracts).
  * Profit target to pass the Combine.
We add prudent self-imposed guards on top: per-trade risk cap, loss-streak halt, and a
"give-back" lock that stops the day after you've surrendered X% of the day's peak.

Typical Combine params (verify current on TopStep before use):
  50K : target 3000, trailingDD 2000, dailyLoss 1000, maxContracts 5
  100K: target 6000, trailingDD 3000, dailyLoss 2000, maxContracts 10
  150K: target 9000, trailingDD 4500, dailyLoss 3000, maxContracts 15
"""
from dataclasses import dataclass, field
import numpy as np

ACCOUNTS = {
    '50K':  dict(start=50_000,  target=3_000, trail_dd=2_000, daily_loss=1_000, max_ct=5),
    '100K': dict(start=100_000, target=6_000, trail_dd=3_000, daily_loss=2_000, max_ct=10),
    '150K': dict(start=150_000, target=9_000, trail_dd=4_500, daily_loss=3_000, max_ct=15),
}


@dataclass
class RiskEngine:
    account: str = '50K'
    per_trade_risk: float = 200.0      # $ max loss budgeted per trade (drives sizing)
    loss_streak_halt: int = 3          # stop the day after N consecutive losers
    giveback_frac: float = 0.5         # stop day if you give back this frac of the day's peak gain
    # state
    equity: float = field(init=False)
    floor: float = field(init=False)
    eod_high: float = field(init=False)
    day_start_equity: float = field(init=False)
    day_peak_equity: float = field(init=False)
    day_pnl: float = 0.0
    streak: int = 0
    locked_today: bool = False
    blown: bool = False
    passed: bool = False

    def __post_init__(self):
        p = ACCOUNTS[self.account]
        self.equity = p['start']
        self.eod_high = p['start']
        self.floor = p['start'] - p['trail_dd']
        self.day_start_equity = p['start']
        self.day_peak_equity = p['start']

    @property
    def p(self):
        return ACCOUNTS[self.account]

    def size_for(self, stop_ticks, tick_value):
        """Contracts so that a full stop ~= per_trade_risk, capped by account max + DD room."""
        risk_per_ct = max(stop_ticks * tick_value, 1e-9)
        n = int(self.per_trade_risk // risk_per_ct)
        room = self.equity - self.floor            # never risk more than distance to the floor
        n = min(n, int(room // risk_per_ct), self.p['max_ct'])
        return max(n, 0)

    def can_trade(self):
        return not (self.blown or self.passed or self.locked_today)

    def on_trade(self, pnl):
        """Apply a closed-trade PnL ($). Returns False if this blows the account."""
        if not self.can_trade():
            return not self.blown
        self.equity += pnl
        self.day_pnl += pnl
        self.day_peak_equity = max(self.day_peak_equity, self.equity)
        self.streak = self.streak + 1 if pnl < 0 else 0
        # hard breach: trailing DD floor
        if self.equity <= self.floor:
            self.blown = True
            return False
        # pass the combine
        if self.equity - self.p['start'] >= self.p['target']:
            self.passed = True
            return True
        # soft locks for the day
        if self.day_pnl <= -self.p['daily_loss']:
            self.locked_today = True
        if self.streak >= self.loss_streak_halt:
            self.locked_today = True
        gain = self.day_peak_equity - self.day_start_equity
        if gain > 0 and (self.day_peak_equity - self.equity) >= self.giveback_frac * gain \
                and self.equity > self.day_start_equity:
            self.locked_today = True       # give-back lock: protect the day's profit
        return True

    def end_of_day(self):
        self.eod_high = max(self.eod_high, self.equity)
        self.floor = min(max(self.floor, self.eod_high - self.p['trail_dd']), self.p['start'])
        self.day_start_equity = self.equity
        self.day_peak_equity = self.equity
        self.day_pnl = 0.0
        self.streak = 0
        self.locked_today = False


def simulate(trade_stream, account='50K', per_trade_risk=200.0, verbose=False):
    """trade_stream: list of (day_index, pnl_dollars). Returns summary dict."""
    eng = RiskEngine(account=account, per_trade_risk=per_trade_risk)
    cur_day = None
    eq_curve = [eng.equity]
    n_taken = 0
    for day, pnl in trade_stream:
        if cur_day is None:
            cur_day = day
        if day != cur_day:
            eng.end_of_day()
            cur_day = day
        if not eng.can_trade():
            continue
        alive = eng.on_trade(pnl)
        n_taken += 1
        eq_curve.append(eng.equity)
        if not alive or eng.passed:
            break
    eq = np.array(eq_curve)
    return dict(account=account, final_equity=eng.equity, blown=eng.blown, passed=eng.passed,
                trades_taken=n_taken, min_equity=float(eq.min()),
                max_dd=float((np.maximum.accumulate(eq) - eq).max()), floor_end=eng.floor)


if __name__ == '__main__':
    # smoke: a coin-flip scalp with slight edge through the engine
    import random
    random.seed(1)
    stream = [(d, (1 if random.random() < 0.52 else -1) * 50) for d in range(40) for _ in range(20)]
    print(simulate(stream, '50K', per_trade_risk=200))
