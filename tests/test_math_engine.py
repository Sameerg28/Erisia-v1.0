import numpy as np
import pandas as pd
import pytest

from src.oracle.oracle_math_engine import QuantitativeEngine


@pytest.fixture
def dummy_market_data() -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=30, freq="D")
    data = {
        "Close": np.linspace(100, 130, 30),
        "High": np.linspace(105, 135, 30),
        "Low": np.linspace(95, 125, 30),
        "Volume": np.arange(1000, 4000, 100),
    }
    return pd.DataFrame(data, index=dates)


def test_enrich_with_indicators(dummy_market_data: pd.DataFrame) -> None:
    engine = QuantitativeEngine()
    enriched_df = engine.enrich_with_indicators(dummy_market_data)

    assert len(enriched_df) == 30

    expected_columns = ["SMA_14", "RSI_14", "MACD_Line", "BB_Upper", "Volume_Surge"]
    for column in expected_columns:
        assert column in enriched_df.columns

    last_row = enriched_df.iloc[-1]
    metrics_dict = engine._row_to_metrics_dict(last_row)
    assert isinstance(metrics_dict, dict)
    assert metrics_dict
    assert "rsi_14" in metrics_dict
