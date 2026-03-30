from typing import Any
import pandas as pd

from backtester import ExecutionSimulator, MarketRegime, PositionDirection, PositionManager, SignalAction


from src.oracle.position_manager import PositionState # make sure this exists

class RecordingPositionManager(PositionManager):
    def __init__(self) -> None:
        super().__init__()
        self.open_long_calls = 0

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

        self.open_long_calls += 1

        return super().open_long(
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
        )

def test_position_manager_tracks_long_position_state() -> None:
    manager = PositionManager()

    position = manager.open_long(
        ticker="AAPL",
        entry_index=5,
        entry_date="2024-01-05",
        entry_price=100.0,
        shares=2.5,
        entry_cost=250.0,
        signal=SignalAction.BUY.value,
        confidence=80,
        reasoning="Momentum confirmed",
        regime=MarketRegime.BULL.value,
    )

    assert manager.is_open() is True
    assert manager.calculate_mark_to_market(105.0) == 262.5
    assert position.direction == PositionDirection.LONG.value
    assert "AAPL" in repr(position)

    closed_position = manager.close()
    assert closed_position is position
    assert manager.is_open() is False
    assert manager.calculate_mark_to_market(105.0) == 0.0


def test_position_manager_tracks_short_mark_to_market() -> None:
    manager = PositionManager()

    manager.open_short(
        ticker="TSLA",
        entry_index=2,
        entry_date="2024-01-02",
        entry_price=100.0,
        shares=3.0,
        entry_cost=300.0,
        signal=SignalAction.SELL.value,
        confidence=75,
        reasoning="Breakdown confirmed",
        regime=MarketRegime.BEAR.value,
    )

    assert manager.is_open() is True
    assert manager.calculate_mark_to_market(90.0) == -270.0


def test_execution_simulator_uses_injected_position_manager() -> None:
    manager = RecordingPositionManager()
    simulator = ExecutionSimulator(
        initial_capital=1_000.0,
        commission=0.0,
        slippage=0.0,
        position_manager=manager,
    )

    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 102.0],
            "signal": [SignalAction.BUY.value, SignalAction.HOLD.value],
            "regime": [MarketRegime.BULL.value, MarketRegime.BULL.value],
            "confidence": [80, 0],
            "reasoning": ["Entry", "Hold"],
        },
        index=pd.date_range("2024-01-01", periods=2, freq="D"),
    )
    frame.attrs["ticker"] = "MSFT"

    trades, equity_curve = simulator.simulate(frame)

    assert manager.open_long_calls == 1
    assert manager.is_open() is False
    assert len(trades) == 1
    assert trades[0].direction == PositionDirection.LONG.value
    assert not equity_curve.empty
