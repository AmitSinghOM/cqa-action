"""Version contract: the action's default analyzer is the one CI tests.

`action.yml` ships a default for the `version` input; `.github/workflows/ci.yml`
installs an exact `cqa-analyzer==X` for the test matrix. The two must name the
same release, and that release must be the one this action version was cut
for, so a bump that touches one file but not the other fails here.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"

# The analyzer release this action version defaults to.
EXPECTED_DEFAULT = "3.5.0"

_ACTION_DEFAULT = re.compile(
    r'^  version:\n(?:    (?!default:).*\n)*?    default: "(?P<v>[^"]+)"', re.MULTILINE
)
_CI_PINS = re.compile(r'"cqa-analyzer==(?P<v>[^"]+)"')


def action_default() -> str:
    match = _ACTION_DEFAULT.search(ACTION.read_text())
    assert match, "action.yml has no `version` input with a quoted default"
    return match.group("v")


def ci_pins() -> list[str]:
    pins = _CI_PINS.findall(CI.read_text())
    assert pins, "ci.yml installs no exact cqa-analyzer pin"
    return pins


def test_action_default_is_the_expected_release():
    assert action_default() == EXPECTED_DEFAULT


def test_ci_pins_match_the_action_default():
    assert set(ci_pins()) == {action_default()}


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-P", "-m", "cqa_analyzer", *args],
        capture_output=True, text=True, check=False,
    )


def test_installed_analyzer_is_the_action_default():
    pytest.importorskip("cqa_analyzer")
    result = _cli("--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(action_default()), result.stdout


def test_rule_catalog_flags_work_on_the_installed_analyzer():
    pytest.importorskip("cqa_analyzer")
    listed = _cli("--list-rules")
    assert listed.returncode == 0, listed.stderr
    assert re.search(r"^PY-SEC-001\b", listed.stdout, re.MULTILINE), listed.stdout[:500]

    explained = _cli("--explain", "PY-SEC-001")
    assert explained.returncode == 0, explained.stderr
    assert "PY-SEC-001" in explained.stdout
    assert "RULES.md" in explained.stdout, explained.stdout
