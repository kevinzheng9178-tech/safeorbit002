"""SafeOrbit 命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from .adapters import integration_snippet, supported_frameworks
from .auth import AuthError, AuthStore, ROLES
from . import backup as backup_mod
from . import eval as eval_mod
from . import infrascan
from . import retention
from . import sca as sca_mod
from .codeaudit import (
    highest_severity as code_highest_severity,
    load_code_report,
    render_code_report_html,
    report_to_json as code_report_to_json,
    report_to_sarif as code_report_to_sarif,
    scan_path as code_scan_path,
)
from .importers import load_trajectory
from .judge import JudgeConfig, JudgeOutcome, judge_trajectory
from .model_settings import load_model_snapshot
from .models import VerdictLevel
from .normalize import normalize
from .pricing import load_pricing
from .report import build_report, render_html, report_to_json
from .rules import AuditContext, RuleEngine, default_rules
from .verdict import score_hits

_VERDICT_TEXT = {
    VerdictLevel.PASS: "通过",
    VerdictLevel.WARN: "有风险",
    VerdictLevel.FAIL: "不通过",
    VerdictLevel.REVIEW: "待复核",
}


def _parse_tools(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safeorbit",
        description="SafeOrbit：Agent 运行轨迹审计",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="审计一份轨迹文件并生成报告")
    audit.add_argument("path", help="轨迹 JSON 文件路径")
    audit.add_argument("--out", default="reports", help="报告输出目录（默认 reports）")
    audit.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "html", "both"],
        default="both",
        help="报告格式（默认 both）",
    )
    audit.add_argument("--budget", type=float, default=None, help="成本预算（美元）")
    audit.add_argument("--allow-tools", default=None, help="工具白名单，逗号分隔")
    audit.add_argument("--loop-threshold", type=int, default=3, help="重复调用判定阈值")
    audit.add_argument(
        "--write-amount-threshold",
        type=float,
        default=None,
        help="写操作金额阈值（超过则告警）",
    )
    audit.add_argument("--redact", action="store_true", help="对轨迹内容做脱敏")
    audit.add_argument("--pricing", default=None, help="自定义价格表 YAML 路径")
    audit.add_argument("--db", default=None, help="读取本机模型设置的 SQLite 路径")
    audit.add_argument("--no-judge", action="store_true", help="跳过语义裁判")
    audit.add_argument("--judge-model", default=None, help="仅单上游模式可使用的裁判模型")
    audit.add_argument("--models-config", default=None, help="本机模型目录 YAML 路径")
    audit.add_argument("--judge-profile", default=None, help="独立裁判模型别名")
    audit.add_argument("--strict", action="store_true", help="判定为不通过时返回非零退出码")

    serve = subparsers.add_parser("serve", help="启动实时审计服务（网关 + 拦截 + 可视化台）")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--mock-upstream", action="store_true", help="使用离线假上游，不联网")
    serve.add_argument("--judge", action="store_true", default=None, help="启用语义裁判（默认关闭）")
    serve.add_argument("--models-config", default=None, help="本机模型目录 YAML 路径")
    serve.add_argument("--judge-profile", default=None, help="独立裁判模型别名")
    serve.add_argument("--upstream-base-url", default=None, help="真实上游地址，如 https://api.deepseek.com")
    serve.add_argument("--db", default=None, help="SQLite 路径")
    serve.add_argument(
        "--mode",
        choices=["shadow", "enforce"],
        default=None,
        help="运行模式：shadow 只记录告警，enforce 才真正阻断（默认 shadow）",
    )

    code_audit = subparsers.add_parser("code-audit", help="静态扫描代码风险并生成报告")
    code_audit.add_argument("path", help="要扫描的文件或目录")
    code_audit.add_argument("--out", default="reports/codeaudit", help="报告输出目录")
    code_audit.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "html", "sarif", "both", "all"],
        default="both",
        help="报告格式（默认 both；all = json + html + sarif）",
    )
    code_audit.add_argument("--baseline", default=None, help="基线报告 JSON，仅输出新增问题")
    code_audit.add_argument("--ignore-file", default=None, help="忽略规则文件（默认读取扫描根目录 .safeorbitignore）")
    code_audit.add_argument(
        "--fail-on",
        choices=["critical", "high", "medium", "low", "none"],
        default="high",
        help="达到该级别时返回非零退出码（默认 high）",
    )

    sca_parser = subparsers.add_parser("sca", help="依赖与供应链扫描")
    sca_parser.add_argument("path", help="项目目录或依赖文件")
    sca_parser.add_argument("--out", default="reports/sca", help="报告输出目录")
    sca_parser.add_argument(
        "--format", dest="output_format", choices=["json", "sarif", "both"], default="both"
    )
    sca_parser.add_argument("--advisories", default=None, help="本地漏洞库 JSON 路径")
    sca_parser.add_argument(
        "--fail-on",
        choices=["critical", "high", "medium", "low", "none"],
        default="high",
    )

    infra_parser = subparsers.add_parser("infra-scan", help="Dockerfile / K8s 配置审计")
    infra_parser.add_argument("path", help="项目目录或配置文件")
    infra_parser.add_argument("--out", default="reports/infra", help="报告输出目录")
    infra_parser.add_argument(
        "--format", dest="output_format", choices=["json", "sarif", "both"], default="both"
    )
    infra_parser.add_argument(
        "--fail-on",
        choices=["critical", "high", "medium", "low", "none"],
        default="high",
    )

    eval_parser = subparsers.add_parser("eval", help="红队与审计效果评测")
    eval_parser.add_argument("--redteam", action="store_true", help="运行内置红队提示集")
    eval_parser.add_argument("--out", default="reports/eval", help="报告输出目录")

    demo_parser = subparsers.add_parser("demo", help="向运行中的安轨注入演示轨迹")
    demo_parser.add_argument("--url", default="http://127.0.0.1:8000", help="安轨服务地址")
    demo_parser.add_argument("--dir", default="fixtures", help="演示轨迹目录（demo_*.json）")
    demo_parser.add_argument("--agent-key", default=None, help="Agent Key（默认读取 SAFEORBIT_AGENT_KEY）")

    evidence_parser = subparsers.add_parser("evidence", help="导出审计证据包")
    evidence_parser.add_argument("action", choices=["export"])
    evidence_parser.add_argument("--out", required=True, help="证据包输出路径（.zip）")
    evidence_parser.add_argument("--db", default=None, help="SQLite 路径")

    retention_parser = subparsers.add_parser("retention", help="数据保留与清理")
    retention_parser.add_argument("--trace-days", type=int, default=90)
    retention_parser.add_argument("--report-versions", type=int, default=10)
    retention_parser.add_argument("--audit-days", type=int, default=365)
    retention_parser.add_argument("--apply", action="store_true", help="真正执行清理")
    retention_parser.add_argument("--db", default=None, help="SQLite 路径")

    backup_parser = subparsers.add_parser("backup", help="在线备份 SQLite 数据库")
    backup_parser.add_argument("--out", default=None, help="备份输出路径（默认带时间戳）")
    backup_parser.add_argument("--db", default=None, help="SQLite 路径")
    backup_parser.add_argument("--list", action="store_true", help="列出已有备份")

    restore_parser = subparsers.add_parser("restore", help="从备份恢复 SQLite 数据库")
    restore_parser.add_argument("backup_path", help="备份文件路径")
    restore_parser.add_argument("--confirm", action="store_true", help="确认执行破坏性恢复")
    restore_parser.add_argument("--db", default=None, help="SQLite 路径")

    probe = subparsers.add_parser("probe", help="生成接入 SafeOrbit 的代码片段")
    probe.add_argument(
        "--framework",
        default="generic",
        choices=supported_frameworks(),
        help="目标框架（默认 generic）",
    )
    probe.add_argument("--url", default="http://127.0.0.1:8000", help="SafeOrbit 服务地址")
    probe.add_argument("--agent-id", default="demo-agent")
    probe.add_argument("--model", default="deepseek-chat")

    mcp_proxy = subparsers.add_parser("mcp-proxy", help="包裹 MCP stdio Server 并审计 tools/call")
    mcp_proxy.add_argument("--url", default="http://127.0.0.1:8000", help="SafeOrbit 服务地址")
    mcp_proxy.add_argument("--agent-id", default="mcp-agent")
    mcp_proxy.add_argument("--trace-id", default=None)
    mcp_proxy.add_argument("--session-id", default=None)
    mcp_proxy.add_argument(
        "--enforce",
        action="store_true",
        help="tools/call 前调用 /tool/check；服务端 enforce 模式下可拒绝执行",
    )
    mcp_proxy.add_argument(
        "--approval-id",
        default=None,
        help="写操作审批单 ID；由安轨审批中心批准后获得，仅匹配同工具同参数",
    )
    mcp_proxy.add_argument(
        "server_cmd",
        nargs=argparse.REMAINDER,
        help="MCP Server 启动命令（放在 -- 之后）",
    )

    mcp_http = subparsers.add_parser(
        "mcp-http-proxy", help="包裹 MCP over HTTP Server 并审计 tools/call"
    )
    mcp_http.add_argument("--listen-host", default="127.0.0.1")
    mcp_http.add_argument("--listen-port", type=int, default=9001)
    mcp_http.add_argument("--upstream", required=True, help="MCP Server 的 HTTP 地址")
    mcp_http.add_argument("--url", default="http://127.0.0.1:8000", help="SafeOrbit 服务地址")
    mcp_http.add_argument("--agent-id", default="mcp-http-agent")
    mcp_http.add_argument("--trace-id", default=None)
    mcp_http.add_argument("--session-id", default=None)
    mcp_http.add_argument("--enforce", action="store_true")
    mcp_http.add_argument("--approval-id", default=None)

    user = subparsers.add_parser("user", help="本地账号管理")
    user.add_argument("action", choices=["init", "reset-password", "create", "list", "disable", "enable"])
    user.add_argument("--username", default=None)
    user.add_argument("--role", choices=ROLES, default="viewer")
    user.add_argument("--db", default=None, help="SQLite 路径")
    return parser


def cmd_audit(args: argparse.Namespace) -> int:
    snapshot = load_model_snapshot(
        getattr(args, "db", None) or os.getenv("SAFEORBIT_DB", "data/safeorbit.db"),
        models_config=getattr(args, "models_config", None),
        judge_profile=getattr(args, "judge_profile", None),
    )
    registry = snapshot.registry
    if args.judge_model and not registry.legacy:
        raise ValueError("目录模式不能使用 --judge-model，请使用 --judge-profile")
    raw = load_trajectory(args.path)
    pricing = load_pricing(args.pricing)
    trajectory = normalize(raw, pricing, redact=args.redact)

    context = AuditContext(
        budget_usd=args.budget,
        allowed_tools=_parse_tools(args.allow_tools),
        loop_threshold=args.loop_threshold,
        write_amount_threshold=args.write_amount_threshold,
    )
    hits = RuleEngine(default_rules()).run(trajectory, context)

    if args.no_judge or not snapshot.judge_enabled:
        judge_outcome = JudgeOutcome(status="skipped", note="已通过 --no-judge 跳过裁判" if args.no_judge else "语义裁判未启用")
    else:
        config = JudgeConfig.from_registry(registry)
        if config is not None and args.judge_model:
            config.model = args.judge_model
        if config is None:
            judge_outcome = JudgeOutcome(
                status="skipped",
                note=("未配置 SAFEORBIT_JUDGE_API_KEY / DEEPSEEK_API_KEY，跳过语义裁判（规则审计不受影响）"
                      if registry.legacy else "未选择裁判 profile，跳过语义裁判（规则审计不受影响）"),
            )
        else:
            # 与规则看到同一份轨迹，避免口径分裂
            judge_outcome = asyncio.run(judge_trajectory(trajectory, config))

    hits.extend(judge_outcome.hits)

    report = build_report(
        trajectory=trajectory,
        hits=hits,
        pricing=pricing,
        judge_status=judge_outcome.status,
        judge_note=judge_outcome.note,
        budget_usd=args.budget,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d%H%M%S")
    base_name = "%s_%s" % (trajectory.trace_id, stamp)
    written: List[Path] = []

    if args.output_format in ("json", "both"):
        json_path = out_dir / (base_name + ".json")
        json_path.write_text(report_to_json(report), encoding="utf-8")
        written.append(json_path)

    if args.output_format in ("html", "both"):
        html_path = out_dir / (base_name + ".html")
        html_path.write_text(render_html(report), encoding="utf-8")
        written.append(html_path)

    _print_result(report, written)
    if args.strict and report.verdict == VerdictLevel.FAIL:
        return 1
    return 0


def _print_result(report, written: List[Path]) -> None:
    print("")
    print("SafeOrbit 审计完成")
    print("  轨迹      : %s" % report.trace_id)
    print("  Agent     : %s" % report.agent_id)
    print("  判定      : %s（%s，%d 分）" % (_VERDICT_TEXT[report.verdict], report.verdict.value, score_hits(report.hits)))
    print("  模型成本  : $%.6f" % report.cost.total_usd)
    print("  发现项    : %d" % len(report.hits))
    if report.hits:
        for hit in report.hits[:5]:
            span = hit.span_ref or "-"
            print("    [%s] %s（span=%s，来源=%s）" % (hit.severity.value.upper(), hit.message, span, hit.source))
        if len(report.hits) > 5:
            print("    ... 其余 %d 项见报告" % (len(report.hits) - 5))
    print("  报告文件  :")
    for path in written:
        print("    %s" % path)


def cmd_code_audit(args: argparse.Namespace) -> int:
    baseline = None
    if args.baseline:
        try:
            baseline = load_code_report(args.baseline)
        except Exception:
            raise ValueError("基线报告无法读取：%s" % args.baseline)
    report = code_scan_path(
        args.path, baseline=baseline, ignore_file=args.ignore_file
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d%H%M%S")
    base_name = "codeaudit_%s" % stamp
    written: List[Path] = []

    if args.output_format in ("json", "both", "all"):
        json_path = out_dir / (base_name + ".json")
        json_path.write_text(code_report_to_json(report), encoding="utf-8")
        written.append(json_path)

    if args.output_format in ("html", "both", "all"):
        html_path = out_dir / (base_name + ".html")
        html_path.write_text(render_code_report_html(report), encoding="utf-8")
        written.append(html_path)

    if args.output_format in ("sarif", "all"):
        sarif_path = out_dir / (base_name + ".sarif")
        sarif_path.write_text(code_report_to_sarif(report), encoding="utf-8")
        written.append(sarif_path)

    print("")
    print("SafeOrbit 代码审计完成")
    print("  扫描根目录: %s" % report.root)
    print("  扫描文件数: %d" % report.files_scanned)
    print(
        "  问题统计  : critical=%d high=%d medium=%d low=%d"
        % (
            report.summary.get("critical", 0),
            report.summary.get("high", 0),
            report.summary.get("medium", 0),
            report.summary.get("low", 0),
        )
    )
    for finding in report.findings[:8]:
        print(
            "    [%s] %s  (%s:%d)"
            % (finding.severity.upper(), finding.rule_name, finding.file, finding.line)
        )
    if len(report.findings) > 8:
        print("    ... 其余 %d 项见报告" % (len(report.findings) - 8))
    print("  报告文件  :")
    for path in written:
        print("    %s" % path)

    if args.fail_on == "none":
        return 0
    order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    worst = code_highest_severity(report)
    return 1 if order.get(worst, 0) >= order.get(args.fail_on, 99) else 0


def cmd_sca(args: argparse.Namespace) -> int:
    report = sca_mod.scan_dependencies(args.path, advisory_path=args.advisories)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d%H%M%S")
    base = "sca_%s" % stamp
    written: List[Path] = []
    if args.output_format in ("json", "both"):
        path = out_dir / (base + ".json")
        path.write_text(sca_mod.report_to_json(report), encoding="utf-8")
        written.append(path)
    if args.output_format in ("sarif", "both"):
        path = out_dir / (base + ".sarif")
        path.write_text(sca_mod.report_to_sarif(report), encoding="utf-8")
        written.append(path)
    print("")
    print("SafeOrbit 依赖扫描完成")
    print("  依赖数量  : %d" % len(report.components))
    print(
        "  漏洞统计  : critical=%d high=%d medium=%d low=%d"
        % (
            report.summary.get("critical", 0),
            report.summary.get("high", 0),
            report.summary.get("medium", 0),
            report.summary.get("low", 0),
        )
    )
    for finding in report.findings[:8]:
        print(
            "    [%s] %s %s  %s  (%s:%d)"
            % (
                finding.severity.upper(),
                finding.package,
                finding.version,
                finding.advisory_id,
                finding.file,
                finding.line,
            )
        )
    print("  报告文件  :")
    for path in written:
        print("    %s" % path)
    if args.fail_on == "none":
        return 0
    worst = max(
        [item.severity for item in report.findings] or ["info"],
        key=lambda item: sca_mod.SEVERITY_ORDER.get(item, 0),
    )
    return 1 if sca_mod.SEVERITY_ORDER.get(worst, 0) >= sca_mod.SEVERITY_ORDER.get(args.fail_on, 99) else 0


def cmd_infra_scan(args: argparse.Namespace) -> int:
    report = infrascan.scan_infra(args.path)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d%H%M%S")
    base = "infra_%s" % stamp
    written: List[Path] = []
    if args.output_format in ("json", "both"):
        path = out_dir / (base + ".json")
        path.write_text(infrascan.report_to_json(report), encoding="utf-8")
        written.append(path)
    if args.output_format in ("sarif", "both"):
        path = out_dir / (base + ".sarif")
        path.write_text(infrascan.report_to_sarif(report), encoding="utf-8")
        written.append(path)
    print("")
    print("SafeOrbit 基础设施扫描完成")
    print("  文件数量  : %d" % report.files_scanned)
    print(
        "  问题统计  : critical=%d high=%d medium=%d low=%d"
        % (
            report.summary.get("critical", 0),
            report.summary.get("high", 0),
            report.summary.get("medium", 0),
            report.summary.get("low", 0),
        )
    )
    for finding in report.findings[:8]:
        print(
            "    [%s] %s  (%s:%d)"
            % (finding.severity.upper(), finding.rule_name, finding.file, finding.line)
        )
    print("  报告文件  :")
    for path in written:
        print("    %s" % path)
    if args.fail_on == "none":
        return 0
    worst = max(
        [item.severity for item in report.findings] or ["info"],
        key=lambda item: infrascan.SEVERITY_ORDER.get(item, 0),
    )
    return 1 if infrascan.SEVERITY_ORDER.get(worst, 0) >= infrascan.SEVERITY_ORDER.get(args.fail_on, 99) else 0


def cmd_eval(args: argparse.Namespace) -> int:
    if not args.redteam:
        print("请指定 --redteam 运行内置红队提示集", file=sys.stderr)
        return 2
    report = eval_mod.run_redteam()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("redteam_%s.json" % datetime.now().strftime("%Y%m%d%H%M%S"))
    path.write_text(eval_mod.report_to_json(report), encoding="utf-8")
    print("")
    print("SafeOrbit 红队评测完成")
    print("  检出率    : %.1f%%（%d/%d）" % (report.detection_rate * 100, report.detected, report.total))
    for item in report.results:
        print("    [%s] %s" % ("命中" if item.detected else "漏检", item.prompt[:60]))
    print("  报告文件  : %s" % path)
    return 0


def cmd_evidence(args: argparse.Namespace) -> int:
    db_path = args.db or os.getenv("SAFEORBIT_DB", "data/safeorbit.db")
    result = retention.export_evidence(db_path, args.out)
    print("证据包已导出：%s" % result["path"])
    print("  轨迹 %d 条，审计日志 %d 条" % (result["trace_count"], result["audit_events"]))
    return 0


def cmd_retention(args: argparse.Namespace) -> int:
    db_path = args.db or os.getenv("SAFEORBIT_DB", "data/safeorbit.db")
    policy = retention.RetentionPolicy(
        trace_days=args.trace_days,
        report_versions=args.report_versions,
        audit_days=args.audit_days,
    )
    result = retention.purge(db_path, policy, dry_run=not args.apply)
    print("保留策略：%s" % json.dumps(result.get("policy"), ensure_ascii=False))
    if result.get("dry_run"):
        print("预演模式：将清理轨迹 %d 条、审计日志 %d 条" % (
            result.get("traces_to_purge", 0), result.get("audit_events_to_purge", 0)
        ))
        print("确认后添加 --apply 执行清理")
    else:
        print("清理完成：%s" % json.dumps(result.get("deleted"), ensure_ascii=False))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    db_path = args.db or os.getenv("SAFEORBIT_DB", "data/safeorbit.db")
    if args.list:
        backups = backup_mod.list_backups(db_path)
        if not backups:
            print("暂无备份。")
            return 0
        for path in backups:
            print(path)
        return 0
    path = backup_mod.backup(db_path, args.out)
    print("数据库已备份：%s" % path)
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    db_path = args.db or os.getenv("SAFEORBIT_DB", "data/safeorbit.db")
    backup_mod.restore(db_path, args.backup_path, confirm=args.confirm)
    print("数据库已恢复：%s" % db_path)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """把 fixtures 下的演示轨迹逐条上报，方便现场复现审计效果。"""

    import httpx

    key = args.agent_key or os.getenv("SAFEORBIT_AGENT_KEY", "").strip()
    if not key:
        print("需要 Agent Key：--agent-key 或环境变量 SAFEORBIT_AGENT_KEY", file=sys.stderr)
        return 2
    paths = sorted(Path(args.dir).glob("demo_*.json"))
    if not paths:
        print("未找到演示轨迹：%s/demo_*.json" % args.dir, file=sys.stderr)
        return 2

    base = args.url.rstrip("/")
    ok = 0
    skipped = 0
    with httpx.Client(timeout=30) as client:
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            response = client.post(
                base + "/v1/ingest",
                json=payload,
                headers={"Authorization": "Bearer " + key},
            )
            if response.status_code == 200:
                body = response.json()
                ok += 1
                print("已注入 %-28s → %s" % (path.name, body.get("trace_id")))
            elif response.status_code == 403:
                # 演示轨迹各自的 Agent 不同，用一把 Key 跑全量时跳过不匹配项。
                skipped += 1
                print("跳过   %-28s （属于其他 Agent：%s）" % (path.name, payload.get("agent_id", "未知")))
            else:
                print("失败 %-28s HTTP %d %s" % (path.name, response.status_code, response.text[:160]))
    print("")
    summary = "共 %d/%d 条进入审计" % (ok, len(paths))
    if skipped:
        summary += "，%d 条因 Agent 不匹配跳过" % skipped
    print(summary + "；打开 %s 查看" % base)
    return 0 if ok else 1


def cmd_probe(args: argparse.Namespace) -> int:
    snippet = integration_snippet(
        args.framework,
        base_url=args.url,
        agent_id=args.agent_id,
        model=args.model,
    )
    print("# SafeOrbit 接入片段 · %s" % args.framework)
    print(snippet)
    return 0


def _read_password() -> str:
    # 容器/CI 场景通过环境变量注入，避免依赖交互式终端。
    injected = os.getenv("SAFEORBIT_USER_PASSWORD")
    if injected:
        return injected
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("新密码（隐藏输入）：")
            confirmation = getpass.getpass("确认新密码（隐藏输入）：")
    except (getpass.GetPassWarning, EOFError):
        raise AuthError("无法安全隐藏输入，请在本机交互终端运行")
    if password != confirmation:
        raise AuthError("两次输入的密码不一致")
    return password


def cmd_user(args: argparse.Namespace) -> int:
    db_path = args.db or os.getenv("SAFEORBIT_DB", "data/safeorbit.db")
    store = AuthStore(db_path)
    if args.action in ("init", "create", "reset-password"):
        if not args.username:
            print("该操作需要 --username", file=sys.stderr)
            return 2
        password = _read_password()
        if args.action == "reset-password":
            store.reset_password(args.username, password)
            print("密码已重置，旧会话已撤销：%s" % args.username)
        else:
            user = (store.bootstrap_admin(args.username, password) if args.action == "init"
                    else store.create_user(args.username, password, role=args.role))
            if user is None:
                # init 幂等：容器重启时不应因管理员已存在而失败。
                print("管理员已存在，跳过初始化：%s" % args.username)
                return 0
            print("已创建用户：%s（角色 %s）" % (user.username, user.role))
        return 0
    if args.action == "list":
        users = store.list_users()
        if not users:
            print("暂无用户。请在本机运行 user init --username <用户名> 显式初始化管理员。")
            return 0
        print("%-16s %-10s %-8s %s" % ("用户名", "角色", "状态", "创建时间"))
        for item in users:
            print(
                "%-16s %-10s %-8s %s"
                % (
                    item["username"],
                    item["role"],
                    "已禁用" if item["disabled"] else "正常",
                    item["created_at"] or "",
                )
            )
        return 0
    if not args.username:
        print("该操作需要 --username", file=sys.stderr)
        return 2
    store.set_disabled(args.username, args.action == "disable")
    print("已%s用户：%s" % ("禁用" if args.action == "disable" else "启用", args.username))
    return 0


def cmd_mcp_proxy(args: argparse.Namespace) -> int:
    from .mcp_proxy import run_proxy

    server_cmd = list(args.server_cmd or [])
    if server_cmd and server_cmd[0] == "--":
        server_cmd = server_cmd[1:]
    return run_proxy(
        server_cmd,
        base_url=args.url,
        agent_id=args.agent_id,
        trace_id=args.trace_id,
        session_id=args.session_id,
        enforce=args.enforce,
        approval_id=args.approval_id,
    )


def cmd_mcp_http_proxy(args: argparse.Namespace) -> int:
    from .mcp_http_proxy import run_http_proxy

    return run_http_proxy(
        args.listen_host,
        args.listen_port,
        args.upstream,
        base_url=args.url,
        agent_id=args.agent_id,
        trace_id=args.trace_id,
        session_id=args.session_id,
        enforce=args.enforce,
        approval_id=args.approval_id,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "audit":
        try:
            return cmd_audit(args)
        except (FileNotFoundError, ValueError) as exc:
            print("审计失败：%s" % exc, file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130

    if args.command == "code-audit":
        try:
            return cmd_code_audit(args)
        except (FileNotFoundError, ValueError) as exc:
            print("代码审计失败：%s" % exc, file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130

    if args.command == "sca":
        try:
            return cmd_sca(args)
        except (FileNotFoundError, ValueError) as exc:
            print("依赖扫描失败：%s" % exc, file=sys.stderr)
            return 2
    if args.command == "infra-scan":
        try:
            return cmd_infra_scan(args)
        except (FileNotFoundError, ValueError) as exc:
            print("基础设施扫描失败：%s" % exc, file=sys.stderr)
            return 2
    if args.command == "eval":
        return cmd_eval(args)
    if args.command == "evidence":
        try:
            return cmd_evidence(args)
        except (FileNotFoundError, ValueError) as exc:
            print("证据导出失败：%s" % exc, file=sys.stderr)
            return 2
    if args.command == "retention":
        try:
            return cmd_retention(args)
        except (FileNotFoundError, ValueError) as exc:
            print("数据清理失败：%s" % exc, file=sys.stderr)
            return 2

    if args.command == "backup":
        try:
            return cmd_backup(args)
        except (FileNotFoundError, ValueError) as exc:
            print("备份失败：%s" % exc, file=sys.stderr)
            return 2

    if args.command == "restore":
        try:
            return cmd_restore(args)
        except (FileNotFoundError, ValueError) as exc:
            print("恢复失败：%s" % exc, file=sys.stderr)
            return 2

    if args.command == "demo":
        try:
            return cmd_demo(args)
        except (FileNotFoundError, ValueError) as exc:
            print("演示注入失败：%s" % exc, file=sys.stderr)
            return 2

    if args.command == "probe":
        return cmd_probe(args)

    if args.command == "user":
        try:
            return cmd_user(args)
        except AuthError as exc:
            print("账号操作失败：%s" % exc, file=sys.stderr)
            return 2

    if args.command == "mcp-proxy":
        try:
            return cmd_mcp_proxy(args)
        except (FileNotFoundError, ValueError) as exc:
            print("MCP 代理启动失败：%s" % exc, file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130

    if args.command == "mcp-http-proxy":
        try:
            return cmd_mcp_http_proxy(args)
        except (FileNotFoundError, ValueError) as exc:
            print("MCP HTTP 代理启动失败：%s" % exc, file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130

    if args.command == "serve":
        if args.models_config:
            os.environ["SAFEORBIT_MODELS_CONFIG"] = args.models_config
        if args.judge_profile:
            os.environ["SAFEORBIT_JUDGE_PROFILE"] = args.judge_profile
        if args.mock_upstream:
            os.environ["SAFEORBIT_UPSTREAM_MOCK"] = "1"
        if args.judge:
            os.environ["SAFEORBIT_JUDGE"] = "on"
        if args.upstream_base_url:
            os.environ["SAFEORBIT_UPSTREAM_BASE_URL"] = args.upstream_base_url
        if args.db:
            os.environ["SAFEORBIT_DB"] = args.db
        if args.mode:
            os.environ["SAFEORBIT_MODE"] = args.mode
        os.environ["SAFEORBIT_PUBLIC_URL"] = "http://%s:%d/v1" % (args.host, args.port)

        try:
            load_model_snapshot(os.getenv("SAFEORBIT_DB", "data/safeorbit.db"))
        except ValueError as exc:
            print("服务配置失败：%s" % exc, file=sys.stderr)
            return 2

        import uvicorn

        uvicorn.run("safeorbit.server:app", host=args.host, port=args.port)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
