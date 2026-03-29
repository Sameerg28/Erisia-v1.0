import pytest
import pandas as pd

from backtester import ExecutionSimulator, MarketRegime, SignalAction
from oracle.portfolio_account import PortfolioAccount
from oracle.position_manager import PositionDirection, PositionState
from oracle.trade_recorder import TradeRecorder


def test_portfolio_account_tracks_cash_and_equity() -> None:
    account = PortfolioAccount(initial_capital=1_000.0)

    assert account.get_cash() == 1_000.0
    assert account.get_total_equity(0.0) == 1_000.0

    account.apply_cash_flow(-250.0)
    assert account.get_cash() == 750.0
    assert account.get_total_equity(100.0) == 850.0

    account.apply_realized_pnl(125.0)
    assert account.get_cash() == 875.0

    account.reset()
    assert account.get_cash() == 1_000.0


def test_trade_recorder_records_long_exit_and_cash_settlement() -> None:
    recorder = TradeRecorder(commission=0.001, slippage=0.0005)
    position = PositionState(
        ticker="AAPL",
        entry_index=0,
        entry_date="2024-01-01",
        entry_price=100.05,
        shares=10.0,
        entry_cost=1_001.5005,
        signal=SignalAction.BUY.value,
        confidence=80,
        reasoning="Trend confirmed",
        regime=MarketRegime.BULL.value,
        direction=PositionDirection.LONG.value,
    )

    trade, cash_delta = recorder.record_trade(
        position=position,
        exit_price=110.0,
        exit_date="2024-01-10",
        exit_reason="SIGNAL",
        hold_bars=9,
    )

    expected_exit_price = 110.0 * (1.0 - 0.0005)
    expected_gross_exit = position.shares * expected_exit_price
    expected_exit_commission = expected_gross_exit * 0.001
    expected_cash_delta = expected_gross_exit - expected_exit_commission
    expected_pnl = expected_cash_delta - position.entry_cost

    assert trade.exit_price == pytest.approx(expected_exit_price)
    assert trade.pnl == pytest.approx(expected_pnl)
    assert trade.pnl_pct == pytest.approx(expected_pnl / position.entry_cost)
    assert cash_delta == pytest.approx(expected_cash_delta)
    assert recorder.trade_history == [trade]


def test_trade_recorder_records_short_exit_and_cash_settlement() -> None:
    recorder = TradeRecorder(commission=0.001, slippage=0.0005, short_borrow_cost_daily=0.0001)
    position = PositionState(
        ticker="TSLA",
        entry_index=0,
        entry_date="2024-01-01",
        entry_price=100.0,
        shares=5.0,
        entry_cost=499.5,
        signal=SignalAction.SELL.value,
        confidence=76,
        reasoning="Breakdown confirmed",
        regime=MarketRegime.BEAR.value,
        direction=PositionDirection.SHORT.value,
    )

    trade, cash_delta = recorder.record_trade(
        position=position,
        exit_price=90.0,
        exit_date="2024-01-04",
        exit_reason="TAKE_PROFIT",
        hold_bars=3,
    )

    expected_exit_price = 90.0 * (1.0 + 0.0005)
    expected_cover_cost = position.shares * expected_exit_price
    expected_exit_commission = expected_cover_cost * 0.001
    expected_borrow_cost = position.shares * position.entry_price * 0.0001 * 3
    expected_total_cover_cost = expected_cover_cost + expected_exit_commission + expected_borrow_cost
    expected_cash_delta = -expected_total_cover_cost
    expected_pnl = position.entry_cost - expected_total_cover_cost

    assert trade.exit_price == pytest.approx(expected_exit_price)
    assert trade.pnl == pytest.approx(expected_pnl)
    assert cash_delta == pytest.approx(expected_cash_delta)
    assert recorder.trade_history == [trade]


def test_execution_simulator_uses_injected_treasury_and_ledger() -> None:
    account = PortfolioAccount(initial_capital=1_000.0)
    recorder = TradeRecorder(commission=0.0, slippage=0.0)
    simulator = ExecutionSimulator(
        initial_capital=1_000.0,
        commission=0.0,
        slippage=0.0,
        portfolio_account=account,
        trade_recorder=recorder,
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

    assert trades is recorder.trade_history
    assert len(trades) == 1
    assert trades[0].direction == PositionDirection.LONG.value
    assert account.get_cash() == pytest.approx(equity_curve.iloc[-1])

    repeat_trades, _ = simulator.simulate(frame)
    assert len(repeat_trades) == 1
