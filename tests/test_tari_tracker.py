import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "desktop_app"))

from tari_tracker import DEFAULT_SETTINGS, normalize_settings, read_settings, write_settings


def test_settings_load_defaults_and_round_trip(tmp_path):
    path = tmp_path / "alert_settings.json"
    assert read_settings(path) == DEFAULT_SETTINGS

    saved = write_settings(path, {"paused": True, "upward_threshold_percent": 30})

    assert saved == {"paused": True, "upward_threshold_percent": 30}
    assert read_settings(path) == saved
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("value", [
    {"paused": False, "upward_threshold_percent": 10},
    {"paused": True, "upward_threshold_percent": 40},
])
def test_valid_settings_are_normalized(value):
    assert normalize_settings(value) == value


@pytest.mark.parametrize("value", [
    {"paused": "false", "upward_threshold_percent": 10},
    {"paused": False, "upward_threshold_percent": 15},
    {"paused": False, "upward_threshold_percent": True},
    [],
])
def test_invalid_settings_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_settings(value)
