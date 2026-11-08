"""依赖与供应链审计：解析依赖清单，匹配本地漏洞库，生成 SBOM/SARIF。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    "node_modules", "venv", ".venv", "env", "dist", "build", "reports", "data",
}
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
DEFAULT_ADVISORIES = Path(__file__).with_name("advisories.json")


class Component(BaseModel):
    name: str
    version: str = ""
    ecosystem: str = "generic"
    spec: str = ""
    file: str
    line: int = 1


class DependencyFinding(BaseModel):
    package: str
    version: str
    ecosystem: str
    advisory_id: str
    severity: str
    summary: str
    fixed_version: str = ""
    file: str
    line: int = 1


class ScaReport(BaseModel):
    report_id: str
    root: str
    generated_at: datetime
    components: List[Component] = Field(default_factory=list)
    findings: List[DependencyFinding] = Field(default_factory=list)
    summary: Dict[str, int] = Field(default_factory=dict)
    sbom: Dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = (
        "依赖审计基于本地漏洞库或指定 OSV 数据；未匹配不等于安全，"
        "仍需结合厂商公告和运行时验证。"
    )


def _version_tuple(value: str) -> Tuple[int, ...]:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")]
    return tuple(numbers or [0])


def _compare(left: str, right: str) -> int:
    left_parts = _version_tuple(left)
    right_parts = _version_tuple(right)
    length = max(len(left_parts), len(right_parts))
    left_parts += (0,) * (length - len(left_parts))
    right_parts += (0,) * (length - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def _matches(version: str, spec: str) -> bool:
    if not version or not spec:
        return False
    for part in [item.strip() for item in spec.split(",") if item.strip()]:
        if part.startswith("^"):
            base = part[1:]
            if _compare(version, base) < 0 or _compare(version, "%d.0.0" % (_version_tuple(base)[0] + 1)) >= 0:
                return False
        elif part.startswith("~="):
            base = part[2:]
            parts = _version_tuple(base)
            upper = "%d.%d.0" % (parts[0], (parts[1] + 1) if len(parts) > 1 else 1)
            if _compare(version, base) < 0 or _compare(version, upper) >= 0:
                return False
        elif part.startswith("~"):
            base = part[1:]
            parts = _version_tuple(base)
            upper = "%d.%d.0" % (parts[0], (parts[1] + 1) if len(parts) > 1 else 1)
            if _compare(version, base) < 0 or _compare(version, upper) >= 0:
                return False
        elif part.startswith("=="):
            if _compare(version, part[2:]) != 0:
                return False
        elif part.startswith("!="):
            if _compare(version, part[2:]) == 0:
                return False
        elif part.startswith(">="):
            if _compare(version, part[2:]) < 0:
                return False
        elif part.startswith("<="):
            if _compare(version, part[2:]) > 0:
                return False
        elif part.startswith(">"):
            if _compare(version, part[1:]) <= 0:
                return False
        elif part.startswith("<"):
            if _compare(version, part[1:]) >= 0:
                return False
        elif part != "*":
            return False
    return True


def _iter_files(root: Path) -> Iterable[Path]:
    names = {"requirements.txt", "package-lock.json", "go.mod", "pyproject.toml"}
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = [item for item in dirs if item not in SKIP_DIRS and not item.startswith(".")]
        for name in files:
            if name in names:
                yield Path(current) / name


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _parse_requirements(path: Path, root: Path) -> List[Component]:
    components: List[Component] = []
    relative = _relative(path, root)
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return components
    pattern = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(==|>=|<=|~=|!=|>|<)?\s*([^\s;#]+)?")
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = pattern.match(stripped)
        if not match:
            continue
        name, operator, version = match.group(1), match.group(2), match.group(3)
        components.append(
            Component(
                name=name,
                version=version if operator == "==" and version else "",
                ecosystem="pypi",
                spec=(operator or "") + (version or ""),
                file=relative,
                line=line_number,
            )
        )
    return components


def _parse_package_lock(path: Path, root: Path) -> List[Component]:
    components: List[Component] = []
    relative = _relative(path, root)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return components
    packages = data.get("packages") or {}
    for key, value in packages.items():
        if not isinstance(value, dict) or not key:
            continue
        name = key.split("node_modules/")[-1]
        if not name:
            continue
        components.append(
            Component(
                name=name,
                version=str(value.get("version") or ""),
                ecosystem="npm",
                spec=str(value.get("version") or ""),
                file=relative,
                line=1,
            )
        )
    return components


def _parse_go_mod(path: Path, root: Path) -> List[Component]:
    components: List[Component] = []
    relative = _relative(path, root)
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return components
    in_require = False
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        match = None
        if in_require:
            match = re.match(r"^([^\s]+)\s+(v[^\s]+)", stripped)
        elif stripped.startswith("require "):
            match = re.match(r"^require\s+([^\s]+)\s+(v[^\s]+)", stripped)
        if not match:
            continue
        module, version = match.group(1), match.group(2)
        components.append(
            Component(
                name=module.rstrip("/").split("/")[-1],
                version=version,
                ecosystem="go",
                spec=version,
                file=relative,
                line=line_number,
            )
        )
    return components


def _parse_pyproject(path: Path, root: Path) -> List[Component]:
    components: List[Component] = []
    relative = _relative(path, root)
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return components
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not re.match(r'^["\']?[A-Za-z0-9_.-]+[>=<~!]=', stripped):
            continue
        match = re.match(r'^["\']?([A-Za-z0-9_.-]+)\s*([>=<~!]=?)\s*([^"\']+)', stripped)
        if not match:
            continue
        name, operator, version = match.groups()
        components.append(
            Component(
                name=name,
                version=version if operator == "==" else "",
                ecosystem="pypi",
                spec=operator + version,
                file=relative,
                line=line_number,
            )
        )
    return components


def _load_advisories(path: Optional[str]) -> List[Dict[str, Any]]:
    target = Path(path) if path else DEFAULT_ADVISORIES
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("advisories") if isinstance(data, dict) else data
    return [item for item in items or [] if isinstance(item, dict)]


def _scan_components(
    components: List[Component], advisories: List[Dict[str, Any]]
) -> List[DependencyFinding]:
    findings: List[DependencyFinding] = []
    for component in components:
        for advisory in advisories:
            package = str(advisory.get("package") or "").lower()
            ecosystem = str(advisory.get("ecosystem") or "").lower()
            if package != component.name.lower():
                continue
            if ecosystem and component.ecosystem and ecosystem != component.ecosystem.lower():
                continue
            affected = str(advisory.get("affected") or "")
            if not _matches(component.version, affected):
                continue
            findings.append(
                DependencyFinding(
                    package=component.name,
                    version=component.version,
                    ecosystem=component.ecosystem,
                    advisory_id=str(advisory.get("id") or "UNKNOWN"),
                    severity=str(advisory.get("severity") or "medium"),
                    summary=str(advisory.get("summary") or ""),
                    fixed_version=str(advisory.get("fixed") or ""),
                    file=component.file,
                    line=component.line,
                )
            )
    findings.sort(
        key=lambda item: (
            -SEVERITY_ORDER.get(item.severity, 0),
            item.package,
            item.file,
            item.line,
        )
    )
    return findings


def _sbom(components: List[Component]) -> Dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": [
            {
                "type": "library",
                "name": item.name,
                "version": item.version or item.spec,
                "properties": [
                    {"name": "safeorbit:ecosystem", "value": item.ecosystem},
                    {"name": "safeorbit:file", "value": item.file},
                ],
            }
            for item in components
        ],
    }


def scan_dependencies(
    path: str,
    *,
    advisory_path: Optional[str] = None,
) -> ScaReport:
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError("扫描路径不存在：%s" % path)
    root = target if target.is_dir() else target.parent
    files = list(_iter_files(root)) if target.is_dir() else [target]
    components: List[Component] = []
    for file_path in files:
        name = file_path.name
        if name == "requirements.txt":
            components.extend(_parse_requirements(file_path, root))
        elif name == "package-lock.json":
            components.extend(_parse_package_lock(file_path, root))
        elif name == "go.mod":
            components.extend(_parse_go_mod(file_path, root))
        elif name == "pyproject.toml":
            components.extend(_parse_pyproject(file_path, root))
    advisories = _load_advisories(advisory_path or os.getenv("SAFEORBIT_SCA_ADVISORIES"))
    findings = _scan_components(components, advisories)
    summary = {key: 0 for key in SEVERITY_ORDER}
    for finding in findings:
        summary[finding.severity] = summary.get(finding.severity, 0) + 1
    return ScaReport(
        report_id="sca-rpt-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:6],
        root=str(root.resolve()),
        generated_at=datetime.now(timezone.utc),
        components=components,
        findings=findings,
        summary=summary,
        sbom=_sbom(components),
    )


def report_to_json(report: ScaReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)


def report_to_sarif(report: ScaReport) -> str:
    levels = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
    results = [
        {
            "ruleId": finding.advisory_id,
            "level": levels.get(finding.severity, "warning"),
            "message": {
                "text": "%s %s：%s" % (
                    finding.package,
                    finding.version,
                    finding.summary or finding.advisory_id,
                )
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": finding.file},
                        "region": {"startLine": finding.line, "startColumn": 1},
                    }
                }
            ],
            "properties": {
                "package": finding.package,
                "version": finding.version,
                "ecosystem": finding.ecosystem,
                "fixed_version": finding.fixed_version,
                "severity": finding.severity,
            },
        }
        for finding in report.findings
    ]
    payload = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": "SafeOrbit SCA"}},
                "results": results,
                "properties": {"report_id": report.report_id, "root": report.root},
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
