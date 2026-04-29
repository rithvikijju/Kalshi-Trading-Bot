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
