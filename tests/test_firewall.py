from pathlib import Path

import pytest

from backtester import BacktestMode, SignalLayer


@pytest.fixture
def signal_layer(tmp_path: Path) -> SignalLayer:
    return SignalLayer(
        mode=BacktestMode.QUANT_ONLY,
        db_path=tmp_path / "test_oracle_memory.db",
    )


def test_valid_llm_signal(signal_layer: SignalLayer) -> None:
    valid_payload = {"action": "BUY", "confidence": 85, "reasoning": "Strong MACD crossover."}
    assert signal_layer._validate_llm_signal(valid_payload) is True


def test_invalid_llm_signal_missing_keys(signal_layer: SignalLayer) -> None:
    invalid_payload = {"action": "BUY", "confidence": 85}
    assert signal_layer._validate_llm_signal(invalid_payload) is False


def test_invalid_llm_signal_bad_action(signal_layer: SignalLayer) -> None:
    invalid_payload = {"action": "MOON", "confidence": 100, "reasoning": "To the moon!"}
    assert signal_layer._validate_llm_signal(invalid_payload) is False


def test_invalid_llm_signal_out_of_bounds_confidence(signal_layer: SignalLayer) -> None:
    invalid_payload = {"action": "BUY", "confidence": 150, "reasoning": "Too confident."}
    assert signal_layer._validate_llm_signal(invalid_payload) is False
