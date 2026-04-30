"""GNN-based triangular arbitrage detection and backtesting."""

from gnn_arbitrage.data import SyntheticFXFeed, FXSnapshot
from gnn_arbitrage.graph import build_rate_graph, edge_index_from_snapshot
from gnn_arbitrage.arbitrage import find_arbitrage_cycles, cycle_profit
from gnn_arbitrage.gnn_model import EdgeArbitrageGNN, train_gnn
from gnn_arbitrage.backtester import Backtester, BacktestResult

__all__ = [
    "SyntheticFXFeed",
    "FXSnapshot",
    "build_rate_graph",
    "edge_index_from_snapshot",
    "find_arbitrage_cycles",
    "cycle_profit",
    "EdgeArbitrageGNN",
    "train_gnn",
    "Backtester",
    "BacktestResult",
]


def __getattr__(name):
    # Live + paper trading depend on ccxt, which is optional. Lazy-import so
    # the rest of the package works without it.
    if name == "CCXTFeed":
        from gnn_arbitrage.live import CCXTFeed
        return CCXTFeed
    if name in ("PaperBroker", "PaperTrade", "RiskCaps"):
        from gnn_arbitrage import paper as _paper
        return getattr(_paper, name)
    raise AttributeError(name)
