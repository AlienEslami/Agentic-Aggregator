from __future__ import annotations

import pandas as pd
import pytest

from scripts.compare_trigger_evaluations import (
    _boolean,
    _cluster_bootstrap,
    _exact_mcnemar,
)


def test_boolean_accepts_csv_boolean_and_binary_values() -> None:
    converted = _boolean(pd.Series(["true", " FALSE ", 1, 0]))
    assert converted.tolist() == [True, False, True, False]


def test_boolean_rejects_unknown_score_values() -> None:
    with pytest.raises(ValueError, match="maybe"):
        _boolean(pd.Series(["true", "maybe"]))


def test_cluster_bootstrap_resamples_whole_sequences() -> None:
    frame = pd.DataFrame(
        {
            "scenario_id": ["a", "a", "b", "b"],
            "wording_variant": ["chat", "chat", "chat", "chat"],
            "agent": [True, True, True, False],
            "rule": [False, True, False, False],
        }
    )
    low, high = _cluster_bootstrap(
        frame, "agent", "rule", repetitions=1_000, seed=26_062_380
    )
    assert low == pytest.approx(0.5)
    assert high == pytest.approx(0.5)


def test_exact_mcnemar_reports_discordance_and_two_sided_probability() -> None:
    result = _exact_mcnemar(
        pd.Series([True, True, True, False, True]),
        pd.Series([False, False, False, True, True]),
    )
    assert result == {
        "agent_correct_rule_wrong": 3,
        "rule_correct_agent_wrong": 1,
        "discordant_pairs": 4,
        "two_sided_exact_p_value": 0.625,
    }
