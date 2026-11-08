"""基础设施配置审计：Dockerfile、Compose 与 Kubernetes YAML 的高风险配置。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", "node_modules",
    "venv", ".venv", "env", "dist", "build", "reports", "data",
}
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


class InfraFinding(BaseModel):
    finding_id: str
    rule_id: str
    rule_name: str
    severity: str
    file: str
    line: int
    message: str
    snippet: str = ""
    remediation: str = ""


class InfraReport(BaseModel):
    report_id: str
    root: str
    generated_at: datetime
    files_scanned: int
    findings: List[InfraFinding] = Field(default_factory=list)
    summary: Dict[str, int] = Field(default_factory=dict)
    disclaimer: str = (
        "基础设施审计基于静态配置模式，不执行容器或集群；"
        "每条结论均锚定 file:line，仍需结合部署环境复核。"
    )


_DOCKER_RULES = [
    (
        "docker_latest_tag",
        "使用 latest 或未固定标签的镜像",
        "medium",
        re.compile(r"(?i)^\s*FROM\s+([^\s:]+)(?::latest)?(?:\s|$)"),
        "固定镜像版本或 digest，避免不可控漂移。",
    ),
    (
        "docker_root_user",
        "容器以 root 用户运行",
        "high",
        re.compile(r"(?i)^\s*USER\s+(root|0)\s*$"),
        "创建非 root 用户并使用 USER 指定。",
    ),
    (
        "docker_privileged",
        "容器启用 privileged",
        "critical",
        re.compile(r"(?i)--privileged\b"),
        "移除 privileged，按需授予最小 capability。",
    ),
    (
        "docker_add_remote",
        "ADD 远程 URL",
        "high",
        re.compile(r"(?i)^\s*ADD\s+https?://"),
        "改用 COPY，并在构建阶段校验下载内容。",
    ),
    (
        "docker_secret_env",
        "Dockerfile 中疑似硬编码密钥",
        "high",
        re.compile(r"(?i)^\s*ENV\s+.*(PASSWORD|SECRET|TOKEN|API_KEY)\s*=\s*[^\s$]+"),
        "改用构建参数或运行时密钥注入，不要写入镜像。",
    ),
    (
        "docker_curl_pipe_shell",
        "下载内容直接交给 shell 执行",
        "high",
        re.compile(r"(?i)(curl|wget)\s+[^|]+\|\s*(sh|bash)"),
        "先下载并校验签名，再执行。",
    ),
]

_YAML_RULES = [
    (
        "k8s_privileged",
        "Pod 启用 privileged",
        "critical",
        re.compile(r"(?i)^\s*privileged\s*:\s*true\s*$"),
        "移除 privileged，使用最小权限 securityContext。",
    ),
    (
        "k8s_root_user",
        "容器以 root 运行",
        "high",
        re.compile(r"(?i)^\s*runAsUser\s*:\s*0\s*$"),
        "设置非 0 的 runAsUser 与 runAsNonRoot。",
    ),
    (
        "k8s_host_path",
        "挂载宿主机路径",
        "high",
        re.compile(r"(?i)^\s*-?\s*hostPath\s*:\s*$"),
        "改用 PVC 或只读 ConfigMap，避免宿主机逃逸面。",
    ),
    (
        "k8s_host_network",
        "使用宿主机网络",
        "high",
        re.compile(r"(?i)^\s*hostNetwork\s*:\s*true\s*$"),
        "移除 hostNetwork，使用 Service 暴露。",
    ),
    (
        "k8s_latest_image",
        "工作负载使用 latest 镜像",
        "medium",
        re.compile(r"(?i)^\s*image\s*:\s*[^\s]+:latest\s*$"),
        "固定镜像版本或 digest。",
    ),
    (
        "k8s_secret_plaintext",
        "配置中疑似明文密钥",
        "high",
        re.compile(r"(?i)^\s*(PASSWORD|SECRET|TOKEN|API_KEY)\s*:\s*[^\s${}]+"),
        "改用 Secret 引用，避免明文写入 YAML。",
    ),
]


def _iter_files(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = [item for item in dirs if item not in SKIP_DIRS and not item.startswith(".")]
        for name in files:
            path = Path(current) / name
            lower = name.lower()
            if lower == "dockerfile" or lower.endswith(".dockerfile") or lower.startswith("dockerfile."):
                yield path
            elif lower.endswith((".yaml", ".yml", ".docker-compose", "docker-compose.yml", "docker-compose.yaml")):
                yield path


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _finding(
    rule_id: str,
    name: str,
    severity: str,
    relative: str,
    line: int,
    snippet: str,
    remediation: str,
) -> InfraFinding:
    return InfraFinding(
        finding_id="infra-" + uuid4().hex[:10],
        rule_id=rule_id,
        rule_name=name,
        severity=severity,
        file=relative,
        line=line,
        message=name,
        snippet=snippet.strip()[:200],
        remediation=remediation,
    )


def _is_kubernetes_yaml(text: str) -> bool:
    """按结构判断是否为 K8s / Compose 清单；注释里的 kind: 不应触发扫描。"""

    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return False
    for document in documents:
        if isinstance(document, dict) and ("kind" in document or "services" in document):
            return True
    return False


def scan_infra(path: str) -> InfraReport:
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError("扫描路径不存在：%s" % path)
    root = target if target.is_dir() else target.parent
    files = list(_iter_files(root)) if target.is_dir() else [target]
    findings: List[InfraFinding] = []

    for file_path in files:
        relative = _relative(file_path, root)
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lower = file_path.name.lower()
        is_docker = lower == "dockerfile" or lower.endswith(".dockerfile") or lower.startswith("dockerfile.")
        rules = _DOCKER_RULES if is_docker else _YAML_RULES
        if not is_docker and not _is_kubernetes_yaml(text):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule_id, name, severity, regex, remediation in rules:
                if regex.search(line):
                    findings.append(
                        _finding(
                            rule_id, name, severity, relative, line_number, line, remediation
                        )
                    )

    findings.sort(
        key=lambda item: (
            -SEVERITY_ORDER.get(item.severity, 0),
            item.file,
            item.line,
            item.rule_id,
        )
    )
    summary = {key: 0 for key in SEVERITY_ORDER}
    for finding in findings:
        summary[finding.severity] = summary.get(finding.severity, 0) + 1
    return InfraReport(
        report_id="infra-rpt-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:6],
        root=str(root.resolve()),
        generated_at=datetime.now(timezone.utc),
        files_scanned=len(files),
        findings=findings,
        summary=summary,
    )


def report_to_json(report: InfraReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)


def report_to_sarif(report: InfraReport) -> str:
    levels = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
    results = [
        {
            "ruleId": finding.rule_id,
            "level": levels.get(finding.severity, "warning"),
            "message": {"text": finding.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": finding.file},
                        "region": {"startLine": finding.line, "startColumn": 1},
                    }
                }
            ],
            "properties": {"severity": finding.severity, "remediation": finding.remediation},
        }
        for finding in report.findings
    ]
    payload = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": "SafeOrbit Infra"}},
                "results": results,
                "properties": {"report_id": report.report_id, "root": report.root},
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
