"""P3 测试：代码审计扫描、报告与 CLI。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from safeorbit.cli import main
from safeorbit.codeaudit import (
    highest_severity,
    render_code_report_html,
    report_to_json,
    scan_path,
)

BAD_CODE = """import hashlib
import pickle
import subprocess

import requests
import yaml

API_KEY = "sk-abcdef1234567890"

def run(user_input, data, stream, url):
    eval(user_input)
    subprocess.run(user_input, shell=True)
    cursor.execute("SELECT * FROM users WHERE id=" + user_input)
    pickle.loads(data)
    yaml.load(stream)
    requests.get(url, verify=False)
    hashlib.md5(data)
"""

CLEAN_CODE = """import os
import subprocess

import yaml

api_key = os.getenv("API_KEY")

def run(user_id, stream):
    yaml.safe_load(stream)
    subprocess.run(["ls", "-la"])
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
"""


def test_scan_finds_anchored_risks(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "bad.py").write_text(BAD_CODE, encoding="utf-8")
    (source / "clean.py").write_text(CLEAN_CODE, encoding="utf-8")

    report = scan_path(str(source))
    rule_ids = {finding.rule_id for finding in report.findings}
    assert "hardcoded_secret" in rule_ids
    assert "eval_exec" in rule_ids
    assert "subprocess_shell_true" in rule_ids
    assert "sql_string_concat" in rule_ids
    assert "unsafe_deserialization" in rule_ids
    assert "unsafe_yaml_load" in rule_ids
    assert "tls_verify_disabled" in rule_ids
    assert "weak_hash" in rule_ids

    assert report.files_scanned == 2
    assert all(finding.file == "bad.py" for finding in report.findings)
    assert all(finding.line > 0 for finding in report.findings)
    assert report.summary["critical"] >= 1
    assert highest_severity(report) == "critical"


def test_clean_code_has_no_findings(tmp_path: Path):
    source = tmp_path / "clean"
    source.mkdir()
    (source / "safe.py").write_text(CLEAN_CODE, encoding="utf-8")
    report = scan_path(str(source))
    assert report.findings == []
    assert highest_severity(report) == "info"


def test_reports_render_with_file_and_line(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "bad.py").write_text(BAD_CODE, encoding="utf-8")
    report = scan_path(str(source))

    payload = json.loads(report_to_json(report))
    assert payload["findings"]
    assert payload["findings"][0]["file"] == "bad.py"

    html = render_code_report_html(report)
    assert "SafeOrbit 代码审计报告" in html
    assert "bad.py:" in html
    assert "硬编码密钥" in html


def test_scan_missing_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        scan_path(str(tmp_path / "missing"))


def test_code_audit_cli_writes_reports_and_exit_code(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "bad.py").write_text(BAD_CODE, encoding="utf-8")
    out = tmp_path / "out"

    assert main(
        ["code-audit", str(source), "--out", str(out), "--format", "both", "--fail-on", "none"]
    ) == 0
    json_files = list(out.glob("*.json"))
    html_files = list(out.glob("*.html"))
    assert len(json_files) == 1
    assert len(html_files) == 1

    assert main(
        ["code-audit", str(source), "--out", str(out), "--format", "json", "--fail-on", "high"]
    ) == 1
