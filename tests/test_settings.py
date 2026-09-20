from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from settings import (
    DEFAULT_UPWARD_ALERT_THRESHOLD,
    parse_alerts_paused,
    parse_upward_alert_threshold,
    threshold_for_direction,
)


@pytest.mark.parametrize("threshold", [10, 20, 30, 40])
def test_allowed_upward_thresholds(threshold):
    assert parse_upward_alert_threshold(str(threshold)) == float(threshold)


@pytest.mark.parametrize("threshold", [0, 15, 50, float("nan"), float("inf")])
def test_rejects_unsupported_upward_thresholds(threshold):
    with pytest.raises(ValueError):
        parse_upward_alert_threshold(str(threshold))


def test_configuration_defaults_and_directional_thresholds():
    assert parse_upward_alert_threshold(None) == DEFAULT_UPWARD_ALERT_THRESHOLD == 10
    assert threshold_for_direction(True, 30) == 30
    assert threshold_for_direction(False, 30) == 10


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "pause"])
def test_alert_pause_values(value):
    assert parse_alerts_paused(value)


@pytest.mark.parametrize("value", [None, "false", "0", "no", "off", "resume"])
def test_alert_resume_values(value):
    assert not parse_alerts_paused(value)


def test_alert_pause_rejects_typo_instead_of_accidentally_resuming():
    with pytest.raises(ValueError):
        parse_alerts_paused("tru")
