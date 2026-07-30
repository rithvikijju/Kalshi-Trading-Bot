"""Base strategy contract.

Every strategy:
  1. Reads OHLCV data + any auxiliary features.
  2. Produces a per-symbol weight DataFrame (index=ts, columns=symbols,
     value=target_weight in [-max, +max]).
  3. Must guarantee no lookahead — every weight at ts uses only data ≤ ts.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import pandas as pd


class Strategy(ABC):
    name: str

    @abstractmethod
    def signal(self, data: pd.DataFrame) -> pd.DataFrame:
        ...

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name}>"
