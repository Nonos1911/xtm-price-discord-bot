import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from workflow_settings import load_settings, resolve_settings, write_github_outputs


def test_workflow_settings_default_and_github_outputs(tmp_path):
    settings_file = tmp_path / "alert_settings.json"
    output_file = tmp_path / "github-output.txt"
    settings = load_settings(settings_file)
    assert settings == {"paused": False, "upward_threshold_percent": 10}

    write_github_outputs(output_file, settings)

    assert output_file.read_text(encoding="utf-8") == "threshold=10\npaused=false\n"


def test_workflow_settings_read_pause_and_threshold(tmp_path):
    settings_file = tmp_path / "alert_settings.json"
    settings_file.write_text(
        json.dumps({"paused": True, "upward_threshold_percent": 30}), encoding="utf-8"
    )
    assert load_settings(settings_file) == {"paused": True, "upward_threshold_percent": 30}


@pytest.mark.parametrize("payload", [
    {"paused": "true", "upward_threshold_percent": 20},
    {"paused": False, "upward_threshold_percent": 25},
    [],
])
def test_workflow_settings_reject_invalid_values(payload):
    with pytest.raises(ValueError):
        resolve_settings(payload)
