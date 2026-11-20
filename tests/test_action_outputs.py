"""The gate step's Python, run exactly as action.yml embeds it.

The report describes untrusted PR code, so its values must not be able to
inject GITHUB_OUTPUT / GITHUB_ENV entries or markup into the job summary.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ACTION = Path(__file__).resolve().parents[1] / "action.yml"


def _heredocs() -> list[str]:
    text = ACTION.read_text()
    blocks = re.findall(r"<<'PY'\n(.*?)\n[ ]*PY\n", text, flags=re.DOTALL)
    return [textwrap.dedent(block) for block in blocks]


def _run(program: str, tmp: Path, report: dict | list | str, code: int, extra: str):
    report_path = tmp / "report.json"
    report_path.write_text(report if isinstance(report, str) else json.dumps(report))
    env = dict(os.environ)
    for name in ("GITHUB_OUTPUT", "GITHUB_ENV"):
        env[name] = str(tmp / name)
        (tmp / name).write_text("")
    env["RUNNER_TEMP"] = str(tmp)
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-P", "-", str(report_path), str(code), extra],
        input=program, text=True, capture_output=True, env=env, check=True, cwd=tmp,
    ).stdout


def _parse_github_file(path: Path) -> dict[str, str]:
    """GitHub's runner semantics for GITHUB_OUTPUT / GITHUB_ENV files."""
    values, lines, i = {}, path.read_text().split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delimiter = line.split("<<", 1)
            body, i = [], i + 1
            while lines[i] != delimiter:
                body.append(lines[i])
                i += 1
            values[name] = "\n".join(body)
        elif "=" in line:
            name, value = line.split("=", 1)
            values[name] = value
        i += 1
    return values


HOSTILE = {
    "architecture_signal_score": "7\nINJECTED=1",
    "configuration_fingerprint": "a" * 64 + "\nNODE_OPTIONS=--require=/tmp/x",
    "analyzer_version": "<script>alert(1)</script>",
    "findings": [
        {
            "rule_id": "x`|<b>",
            "severity": "warning\n## fake heading",
            "message": "see [link](https://evil.example) @team <img src=x onerror=1> | cell \u2028 x",
            "location": {"path": "a|b`<i>.py", "line": "3\r\nEVIL=1"},
        },
        "not a dict",
    ],
}


def test_there_are_two_embedded_programs():
    assert len(_heredocs()) == 2


def test_outputs_and_env_cannot_be_injected(tmp_path: Path):
    outputs_program = _heredocs()[0]
    _run(outputs_program, tmp_path, HOSTILE, 4, "out.sarif\nEVIL=1")
    outputs = _parse_github_file(tmp_path / "GITHUB_OUTPUT")
    env = _parse_github_file(tmp_path / "GITHUB_ENV")
    assert set(outputs) == {"exit-code", "verdict", "score", "findings", "configuration-fingerprint", "sarif-file"}
    assert set(env) == {f"CQA_{k.upper().replace('-', '_')}" for k in outputs}
    assert outputs["score"] == ""  # non-numeric score dropped
    assert outputs["configuration-fingerprint"] == ""  # not a bare sha256
    assert outputs["sarif-file"] == "out.sarif\nEVIL=1"  # carried verbatim, as one value
    assert outputs["exit-code"] == "4" and outputs["verdict"] == "fail"
    results = json.loads((tmp_path / "cqa-results.json").read_text())
    assert results["exit_code"] == 4 and results["findings"] == 2


def test_well_formed_report_values_are_unchanged(tmp_path: Path):
    good = {"architecture_signal_score": 9.5, "configuration_fingerprint": "b" * 64, "findings": []}
    _run(_heredocs()[0], tmp_path, good, 0, "r.sarif")
    outputs = _parse_github_file(tmp_path / "GITHUB_OUTPUT")
    assert outputs["score"] == "9.5"
    assert outputs["configuration-fingerprint"] == "b" * 64
    assert outputs["findings"] == "0" and outputs["verdict"] == "pass"


def test_summary_renders_untrusted_values_as_inert_text(tmp_path: Path):
    summary = _run(_heredocs()[1], tmp_path, HOSTILE, 4, "manifest.json")
    for raw in ("<script", "<img", "<b>", "<i>", "](", "@team", "`|", "## fake", "EVIL=1\n"):
        assert raw not in summary, raw
    table = [line for line in summary.splitlines() if line.startswith("| ")]
    assert len(table) == 2  # header + one dict finding; the string entry is dropped
    assert all(line.count("|") == 5 for line in table)
    assert "\u2028" not in summary
    assert "configuration fingerprint" not in summary  # invalid fingerprint omitted


def test_summary_survives_non_object_report(tmp_path: Path):
    summary = _run(_heredocs()[1], tmp_path, "[1, 2]", 2, "")
    assert "nothing analyzed" in summary
