from __future__ import annotations


class PortfolioAccount:
    """Manage cash balance and equity calculations for a simulation run."""

    def __init__(self, initial_capital: float) -> None:
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)

    def reset(self) -> None:
        self.cash = self.initial_capital

    def get_cash(self) -> float:
        return self.cash

    def apply_realized_pnl(self, realized_pnl: float) -> float:
        """Apply a realized cash delta to the account balance."""
        self.cash += realized_pnl
        return self.cash

    def apply_cash_flow(self, cash_flow: float) -> float:
        return self.apply_realized_pnl(cash_flow)

    def get_total_equity(self, mark_to_market_unrealized: float) -> float:
        return self.cash + mark_to_market_unrealized


__all__ = ["PortfolioAccount"]
