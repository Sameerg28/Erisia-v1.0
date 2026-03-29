from __future__ import annotations

from dataclasses import dataclass

from .position_manager import PositionDirection, PositionState


@dataclass(slots=True)
class TradeRecord:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    pnl_pct: float
    signal: str
    confidence: int
    reasoning: str
    regime: str
    hold_bars: int
    exit_reason: str
    direction: str


class TradeRecorder:
    """Calculate exit settlement math and archive trade records."""

    def __init__(
        self,
        commission: float,
        slippage: float,
        short_borrow_cost_daily: float = 0.0,
    ) -> None:
        self.commission = commission
        self.slippage = slippage
        self.short_borrow_cost_daily = short_borrow_cost_daily
        self.trade_history: list[TradeRecord] = []

    def reset(self) -> None:
        self.trade_history = []

    def record_trade(
        self,
        position: PositionState,
        exit_price: float,
        exit_date: str,
        exit_reason: str,
        hold_bars: int,
    ) -> tuple[TradeRecord, float]:
        effective_exit_price = max(self._apply_exit_slippage(position.direction, exit_price), 0.0)

        if position.direction == PositionDirection.SHORT.value:
            borrow_cost = position.shares * position.entry_price * self.short_borrow_cost_daily * hold_bars
            gross_cover_cost = position.shares * effective_exit_price
            exit_commission = gross_cover_cost * self.commission
            total_cover_cost = gross_cover_cost + exit_commission + borrow_cost
            account_cash_delta = -total_cover_cost
            pnl = position.entry_cost - total_cover_cost
        else:
            gross_exit_value = position.shares * effective_exit_price
            exit_commission = gross_exit_value * self.commission
            net_exit_value = gross_exit_value - exit_commission
            account_cash_delta = net_exit_value
            pnl = net_exit_value - position.entry_cost

        pnl_pct = pnl / position.entry_cost if position.entry_cost > 0.0 else 0.0
        trade = TradeRecord(
            ticker=position.ticker,
            entry_date=position.entry_date,
            exit_date=exit_date,
            entry_price=position.entry_price,
            exit_price=effective_exit_price,
            shares=position.shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
            signal=position.signal,
            confidence=position.confidence,
            reasoning=position.reasoning,
            regime=position.regime,
            hold_bars=hold_bars,
            exit_reason=exit_reason,
            direction=position.direction,
        )
        self.trade_history.append(trade)
        return trade, account_cash_delta

    def _apply_exit_slippage(self, direction: str, exit_price: float) -> float:
        if direction == PositionDirection.SHORT.value:
            return exit_price * (1.0 + self.slippage)
        return exit_price * (1.0 - self.slippage)


__all__ = ["TradeRecord", "TradeRecorder"]
