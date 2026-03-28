from __future__ import annotations

import logging
import math
import os
import typing

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    _ALPACA_IMPORT_ERROR: typing.Optional[Exception] = None
except ImportError as exc:  # pragma: no cover
    TradingClient = None  # type: ignore[assignment]
    OrderSide = None  # type: ignore[assignment]
    TimeInForce = None  # type: ignore[assignment]
    MarketOrderRequest = None  # type: ignore[assignment]
    _ALPACA_IMPORT_ERROR = exc


def _load_env_file(path: str, logger: logging.Logger) -> None:
    """
    Minimal .env loader (avoids extra deps). Populates os.environ for keys not already set.
    Expected format: KEY=VALUE, with optional quotes and comments.
    """
    if not path or not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                if not key:
                    continue

                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]

                if key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:
        logger.warning("Failed to load .env from %s: %s", path, exc)


class ExecutionBroker:
    def __init__(self, logger: typing.Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("oracle.execution")
        self.client: typing.Any = None
        self.is_active = False

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env_path = os.path.join(base_dir, "config", ".env")
        _load_env_file(env_path, self.logger)

        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")

        if not api_key or not secret_key:
            self.logger.warning(
                "Alpaca keys not configured. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in config/.env."
            )
            return

        if TradingClient is None:
            self.logger.warning(
                "alpaca-py is not installed; execution disabled. Install alpaca-py to enable trading. (%s)",
                _ALPACA_IMPORT_ERROR,
            )
            return

        try:
            self.client = TradingClient(api_key, secret_key, paper=True)
            self.is_active = True
        except Exception as exc:
            self.logger.warning("Failed to initialize Alpaca TradingClient; execution disabled: %s", exc)

    def get_account_status(self) -> dict:
        if not self.is_active or self.client is None:
            return {"equity": 0.0, "buying_power": 0.0}

        try:
            account = self.client.get_account()
            equity_raw = getattr(account, "equity", 0.0)
            buying_power_raw = getattr(account, "buying_power", 0.0)
            try:
                equity = float(equity_raw)
            except Exception:
                equity = 0.0
            try:
                buying_power = float(buying_power_raw)
            except Exception:
                buying_power = 0.0
            return {"equity": equity, "buying_power": buying_power}
        except Exception as exc:
            self.logger.warning("Failed to fetch account status: %s", exc)
            return {"equity": 0.0, "buying_power": 0.0}

    def execute_trade(self, ticker: str, action: str, confidence: int, current_price: float) -> str:
        if not self.is_active or self.client is None:
            return "ExecutionBroker inactive: Alpaca credentials not configured."

        action_norm = str(action).strip().upper()
        if action_norm in {"HOLD", "ERROR"}:
            return "No trade executed (HOLD)."

        if action_norm not in {"BUY", "SELL"}:
            return "No trade executed (HOLD)."

        try:
            confidence_val = int(confidence)
        except Exception:
            confidence_val = 0

        if confidence_val < 50:
            return "Confidence too low for execution."

        try:
            price_val = float(current_price)
        except Exception:
            return "Invalid current price for execution."

        if price_val <= 0:
            return "Invalid current price for execution."

        account = self.get_account_status()
        buying_power = float(account.get("buying_power", 0.0))

        risk_percent = 0.05 if confidence_val < 70 else 0.10
        allocated_cash = buying_power * risk_percent
        qty = int(math.floor(allocated_cash / price_val))
        if qty <= 0:
            return "Insufficient funds to buy 1 share."

        side = OrderSide.BUY if action_norm == "BUY" else OrderSide.SELL  # type: ignore   
        ticker_norm = str(ticker).strip().upper()

        market_order_data = MarketOrderRequest(  # type: ignore
            symbol=ticker_norm,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.GTC,  # type: ignore
        )

        try:
            self.client.submit_order(order_data=market_order_data)
            return f"Successfully placed {action_norm} order for {qty} shares of {ticker_norm}"
        except Exception as exc:
            return f"Order submission failed: {exc}"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    broker = ExecutionBroker()
    print(broker.get_account_status())
