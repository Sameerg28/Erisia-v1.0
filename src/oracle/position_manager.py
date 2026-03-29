from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PositionDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass(slots=True)
class PositionState:
    ticker: str
    entry_index: int
    entry_date: str
    entry_price: float
    shares: float
    entry_cost: float
    signal: str
    confidence: int
    reasoning: str
    regime: str
    direction: str

    def __repr__(self) -> str:
        return (
            "PositionState("
            f"ticker={self.ticker!r}, entry_index={self.entry_index}, "
            f"entry_date={self.entry_date!r}, entry_price={self.entry_price:.6f}, "
            f"shares={self.shares:.6f}, entry_cost={self.entry_cost:.6f}, "
            f"direction={self.direction!r}, confidence={self.confidence}, regime={self.regime!r}"
            ")"
        )


class PositionManager:
    """Track the currently open simulated position."""

    def __init__(self, position: PositionState | None = None) -> None:
        self._position = position

    @property
    def position(self) -> PositionState | None:
        return self._position

    def get_position(self) -> PositionState | None:
        return self._position

    def is_open(self) -> bool:
        return self._position is not None

    def reset(self) -> None:
        self._position = None

    def close(self) -> PositionState | None:
        closed_position = self._position
        self._position = None
        return closed_position

    def close_position(self) -> PositionState | None:
        return self.close()

    def calculate_mark_to_market(self, close_price: float) -> float:
        if self._position is None:
            return 0.0
        if self._position.direction == PositionDirection.SHORT.value:
            return -(self._position.shares * close_price)
        return self._position.shares * close_price

    def open_long(
        self,
        *,
        ticker: str,
        entry_index: int,
        entry_date: str,
        entry_price: float,
        shares: float,
        entry_cost: float,
        signal: str,
        confidence: int,
        reasoning: str,
        regime: str,
    ) -> PositionState:
        return self._open(
            ticker=ticker,
            entry_index=entry_index,
            entry_date=entry_date,
            entry_price=entry_price,
            shares=shares,
            entry_cost=entry_cost,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            regime=regime,
            direction=PositionDirection.LONG,
        )

    def open_short(
        self,
        *,
        ticker: str,
        entry_index: int,
        entry_date: str,
        entry_price: float,
        shares: float,
        entry_cost: float,
        signal: str,
        confidence: int,
        reasoning: str,
        regime: str,
    ) -> PositionState:
        return self._open(
            ticker=ticker,
            entry_index=entry_index,
            entry_date=entry_date,
            entry_price=entry_price,
            shares=shares,
            entry_cost=entry_cost,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            regime=regime,
            direction=PositionDirection.SHORT,
        )

    def _open(
        self,
        *,
        ticker: str,
        entry_index: int,
        entry_date: str,
        entry_price: float,
        shares: float,
        entry_cost: float,
        signal: str,
        confidence: int,
        reasoning: str,
        regime: str,
        direction: PositionDirection,
    ) -> PositionState:
        if self._position is not None:
            raise RuntimeError(f"Cannot open {direction.value} while {self._position.direction} is already active.")

        self._position = PositionState(
            ticker=ticker,
            entry_index=entry_index,
            entry_date=entry_date,
            entry_price=entry_price,
            shares=shares,
            entry_cost=entry_cost,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            regime=regime,
            direction=direction.value,
        )
        return self._position


__all__ = [
    "PositionDirection",
    "PositionManager",
    "PositionState",
]
