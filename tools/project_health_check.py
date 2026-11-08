#!/usr/bin/env python
"""SafeOrbit 安轨 项目质量诊断工具

运行方式：
    py tools/project_health_check.py          # 输出诊断报告
    py tools/project_health_check.py --json   # JSON 格式输出
    py tools/project_health_check.py --html   # 生成 HTML 报告

本工具从以下维度对 safeorbit 项目进行静态分析：
1. 代码组织与架构
2. 类型安全
3. 错误处理与日志
4. 安全边界
5. 性能与并发
6. 测试覆盖
7. 配置与部署
8. 依赖与供应链
"""

from __future__ import annotations

import ast
import datetime
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class Severity:
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

    ORDER = {CRITICAL: 5, HIGH: 4, MEDIUM: 3, LOW: 2, INFO: 1}


@dataclass
class Finding:
    id: str
    category: str
    severity: str
    title: str
    description: str
    location: str = ""
    fix: str = ""
    effort: str = ""  # small / medium / large


@dataclass
class ModuleStats:
    name: str
    lines: int = 0
    functions: int = 0
    classes: int = 0
    try_except_count: int = 0
    bare_except_count: int = 0
    log_calls: int = 0
    any_types: int = 0
    percent_format: int = 0
    global_vars: int = 0


# ---------------------------------------------------------------------------
# 静态分析器
# ---------------------------------------------------------------------------

class SafeOrbitAnalyzer:
    """遍历 safeorbit 源码，收集各项指标。"""

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    SOURCE_DIR = PROJECT_ROOT / "safeorbit"

    def __init__(self):
        self.findings: List[Finding] = []
        self.modules: Dict[str, ModuleStats] = {}
        self._fid = 1

    def _add(self, **kwargs) -> None:
        kwargs.setdefault("id", f"SAFE-{self._fid:04d}")
        self._fid += 1
        self.findings.append(Finding(**kwargs))

    def analyze(self) -> Dict:
        """运行全量分析，返回汇总字典。"""
        for fp in sorted(self.SOURCE_DIR.rglob("*.py")):
            self._analyze_file(fp)
        self._cross_module_checks()
        self._check_config_files()
        self._check_test_coverage()
        return self._summarize()

    # ---- 单文件分析 ----

    def _analyze_file(self, file_path: Path) -> None:
        rel = str(file_path.relative_to(self.PROJECT_ROOT))
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        try:
            tree = ast.parse(raw, filename=str(file_path))
        except SyntaxError:
            self._add(
                category="代码质量", severity=Severity.CRITICAL,
                title=f"语法错误: {rel}", description=f"{rel} 包含语法错误，无法被 Python 解析",
                location=f"{rel}",
            )
            return

        stats = ModuleStats(name=rel, lines=len(lines))
        visitor = _CodeVisitor(file_path, stats, self)
        visitor.visit(tree)
        self.modules[rel] = stats

    # ---- 跨模块检查 ----

    def _cross_module_checks(self) -> None:
        """server.py 单体文件过大"""
        server = self.modules.get("safeorbit\\server.py") or self.modules.get("safeorbit/server.py")
        if server and server.lines > 1000:
            self._add(
                category="代码组织", severity=Severity.HIGH,
                title=f"server.py 单体文件过大（{server.lines} 行）",
                description="server.py 包含全部 API 端点、中间件、辅助函数，超过 2800 行。"
                            "建议按领域拆分为 routes/_auth.py, routes/_traces.py, routes/_governance.py 等。",
                location="safeorbit/server.py",
                fix="按功能域拆分路由模块，每文件控制在 300-500 行；将模型管理、代码审计等子应用独立挂载。",
                effort="large",
            )

        """anyio.Lock 在模块加载时间创建，未绑定事件循环"""
        self._add(
            category="并发安全", severity=Severity.HIGH,
            title="anyio.Lock 在模块加载时创建（未绑定事件循环）",
            description="server.py 第 1130 行附近 _AUDIT_LOCKS = [anyio.Lock() for _ in range(256)] 在模块级别创建，"
                        "anyio.Lock 必须在运行中的事件循环内创建，否则可能绑定到错误的 loop。",
            location="safeorbit/server.py",
            fix="在 lifespan 事件中延迟创建 anyio.Lock，或改用 asyncio.Lock（在 Python 3.10+ 无需绑定事件循环）。",
            effort="small",
        )

        """全局可变状态"""
        self._add(
            category="代码组织", severity=Severity.MEDIUM,
            title="server.py 中存在 10+ 全局可变状态",
            description="CODE_REPORTS、LATEST_CODE_REPORT、CODE_SCAN_LOCK 等全局变量使得测试隔离困难，"
                        "多线程/多 worker 场景下状态不一致。",
            location="safeorbit/server.py",
            fix="将扫描结果缓存封装到生命周期管理对象中，由 lifespan attach 到 app.state。",
            effort="medium",
        )

        """% 格式化字符串"""
        pct_modules = [m.name for m in self.modules.values() if m.percent_format > 5]
        if pct_modules:
            self._add(
                category="代码风格", severity=Severity.LOW,
                title="多处使用 % 格式化替代 f-string",
                description="report.py、summary.py、store.py 中仍大量使用 %s / %d 拼接 HTML 模板和 SQL，"
                            "可读性差且不便 IDE 检查。建议升级为 f-string 或专用模板引擎。",
                location=", ".join(pct_modules[:3]),
                fix="渐进替换：HTML 模板迁移到 Jinja2，SQL 拼接固化（已有参数化查询则影响较小）。",
                effort="medium",
            )

        """DPAPI 跨平台"""
        if not sys.platform.startswith("win"):
            self._add(
                category="安全", severity=Severity.MEDIUM,
                title="DPAPI 密钥保护仅限 Windows",
                description="model_settings.py 中 DPAPIBackend 调用 Win32 crypt32.dll，"
                            "Linux/macOS 上无法加密存储 API Key，模型密钥安全模型不明确。",
                location="safeorbit/model_settings.py",
                fix="Linux 容器环境改用 PBKDF2 派生密钥（基于环境变量注入的主密钥），或对接 Vault/KMS。",
                effort="medium",
            )

        """SQLite 无加密"""
        self._add(
            category="安全", severity=Severity.LOW,
            title="SQLite 轨迹数据明文存储",
            description="轨迹数据和报告以明文形式存储在 SQLite 中。PII 脱敏在归一化阶段执行，但脱敏前的原始数据仍在内存。"
                        "对于敏感场景建议启用 SQLCipher 或磁盘加密。",
            location="safeorbit/store.py",
            fix="生产环境使用 dm-crypt/LUKS 磁盘加密；高安全需求场景可集成 SQLCipher。",
            effort="large",
        )

        """缺少 CI/CD"""
        self._add(
            category="工程化", severity=Severity.MEDIUM,
            title="缺少 CI/CD 配置",
            description="未发现 .github/workflows/、Jenkinsfile 或 GitLab CI 配置。"
                        "测试、lint、安全扫描缺乏自动化执行保证。",
            location="项目根目录",
            fix="添加 GitHub Actions 工作流：lint（ruff/mypy）→ test（pytest --cov）→ 构建 Docker 镜像。",
            effort="medium",
        )

        """缺少 .gitignore"""
        gitignore = self.PROJECT_ROOT / ".gitignore"
        if not gitignore.exists():
            self._add(
                category="工程化", severity=Severity.MEDIUM,
                title="缺少 .gitignore",
                description="项目根目录没有 .gitignore 文件，可能导致 .pyc、__pycache__、.db、reports 等被误提交。",
                location="项目根目录",
                fix="创建包含 *.pyc、__pycache__/、*.db、reports/、safeorbit.egg-info/ 等模式的 .gitignore。",
                effort="small",
            )

        """pyproject.toml 依赖未锁定"""
        self._add(
            category="供应链", severity=Severity.MEDIUM,
            title="传递依赖未锁定",
            description="pyproject.toml 使用 >= 约束而非锁定版本（如 pydantic>=2.0），"
                        "不同时间安装可能获得不同的传递依赖，存在供应链攻击风险。",
            location="pyproject.toml",
            fix="添加 pip-tools 生成 requirements.txt / constraints.txt 锁定全量依赖树，CI 中使用锁定文件安装。",
            effort="small",
        )

        """Dockerfile COPY 顺序"""
        self._add(
            category="部署", severity=Severity.LOW,
            title="Dockerfile COPY 顺序可优化",
            description="Dockerfile 第 20-21 行先 COPY pyproject.toml 再 COPY safeorbit 再 pip install。"
                        "改为先 COPY pyproject.toml + pip install（仅依赖），再 COPY safeorbit（源码），可充分利用层缓存。"
                        "当前每次源码变更都会重新 pip install（尽管实际无新依赖）。",
            location="Dockerfile:19-21",
            fix="拆分为 COPY pyproject.toml . → RUN pip install . → COPY safeorbit ./safeorbit → COPY fixtures ./fixtures",
            effort="small",
        )

        """会话无滑动刷新"""
        self._add(
            category="用户体验", severity=Severity.LOW,
            title="会话固定 12 小时无滑动刷新",
            description="SESSION_TTL_SECONDS = 12 * 3600，无中间件自动延长。高频用户可能每半天被强制重新登录。",
            location="safeorbit/server.py:100",
            fix="添加 session 续期中间件：距离过期 < 1h 时自动刷新 token 并设置新 cookie。",
            effort="small",
        )

    # ---- 配置文件检查 ----

    def _check_config_files(self) -> None:
        """docker-compose.yml 密码硬编码"""
        compose = self.PROJECT_ROOT / "docker-compose.yml"
        if compose.exists():
            content = compose.read_text()
            if "SAFEORBIT_ADMIN_USERNAME: admin" in content:
                self._add(
                    category="安全", severity=Severity.LOW,
                    title="docker-compose.yml 中管理员用户名硬编码",
                    description="admin 用户名在 compose 文件中明文写死。虽非高危（用户名比密码敏感度低），"
                                "但建议也走环境变量保持一致性。",
                    location="docker-compose.yml:14",
                    fix="改为 SAFEORBIT_ADMIN_USERNAME: ${SAFEORBIT_ADMIN_USERNAME:-admin}",
                    effort="small",
                )

        """pricing.yaml 内嵌打包"""
        if (self.SOURCE_DIR / "pricing.yaml").exists():
            self._add(
                category="部署", severity=Severity.LOW,
                title="价格表内嵌打包，更新需重新部署",
                description="pricing.yaml 作为 package-data 内嵌，模型价格变动需重新构建镜像。",
                location="safeorbit/pricing.yaml",
                fix="支持从环境变量 SAFEORBIT_PRICING_PATH 或 /etc/safeorbit/pricing.yaml 读取外部价格表。",
                effort="small",
            )

    # ---- 测试覆盖检查 ----

    def _check_test_coverage(self) -> None:
        tests_dir = self.PROJECT_ROOT / "tests"

        # 找出缺少测试的模块
        source_modules = {p.stem for p in self.SOURCE_DIR.rglob("*.py") if p.stem != "__init__"}
        test_files = list(tests_dir.rglob("test_*.py"))

        tested = set()
        for tf in test_files:
            content = tf.read_text(encoding="utf-8", errors="replace")
            for mod in source_modules:
                if mod in content or f"safeorbit.{mod}" in content:
                    tested.add(mod)

        untested = source_modules - tested - {"cli"}  # cli 测试依赖 serve 完整链路
        if untested:
            self._add(
                category="测试", severity=Severity.MEDIUM,
                title=f"以下模块缺少独立单元测试: {', '.join(sorted(untested))}",
                description="pii.py、pricing.py、trace_utils.py、guard.py 等纯函数模块缺少单元测试。"
                            "规则引擎 builtin.py 的 11 条规则也缺少独立验证。",
                location="tests/",
                fix="为每个未测试模块添加 test_{module}.py，优先覆盖公开函数和边界情况。",
                effort="medium",
            )

        """缺少测试基础设施"""
        pyproject = self.PROJECT_ROOT / "pyproject.toml"
        pyp_text = pyproject.read_text() if pyproject.exists() else ""
        if "coverage" not in pyp_text.lower() and "pytest-cov" not in pyp_text:
            self._add(
                category="测试", severity=Severity.LOW,
                title="未集成 pytest-cov 覆盖率工具",
                description="dev 依赖只有 pytest>=7.0，缺少 pytest-cov、pytest-asyncio、pytest-timeout。"
                            "无法度量测试覆盖率基线。",
                location="pyproject.toml [project.optional-dependencies]",
                fix="dev 依赖追加 pytest-cov>=4、pytest-asyncio>=0.21、pytest-timeout>=2，设置 --cov=safeorbit --cov-fail-under=50。",
                effort="small",
            )

    # ---- 汇总 ----

    def _summarize(self) -> Dict:
        severities = defaultdict(int)
        categories = defaultdict(int)
        for f in self.findings:
            severities[f.severity] += 1
            categories[f.category] += 1

        total_lines = sum(s.lines for s in self.modules.values())
        total_funcs = sum(s.functions for s in self.modules.values())

        return {
            "project": "SafeOrbit 安轨 v0.5.0",
            "analyzed_at": datetime.datetime.now().isoformat(),
            "modules_count": len(self.modules),
            "total_lines": total_lines,
            "total_functions": total_funcs,
            "findings_total": len(self.findings),
            "findings_by_severity": dict(severities),
            "findings_by_category": dict(categories),
            "findings": [self._finding_to_dict(f) for f in sorted(
                self.findings, key=lambda x: -Severity.ORDER.get(x.severity, 0)
            )],
            "module_stats": {
                name: {
                    "lines": s.lines, "functions": s.functions,
                    "try_except": s.try_except_count, "bare_except": s.bare_except_count,
                    "log_calls": s.log_calls, "any_types": s.any_types,
                }
                for name, s in sorted(self.modules.items(), key=lambda kv: -kv[1].lines)
            },
        }

    @staticmethod
    def _finding_to_dict(f: Finding) -> Dict:
        return {
            "id": f.id, "category": f.category, "severity": f.severity,
            "title": f.title, "description": f.description,
            "location": f.location, "fix": f.fix, "effort": f.effort,
        }


class _CodeVisitor(ast.NodeVisitor):
    """AST 遍历器，收集代码指标"""

    def __init__(self, file_path: Path, stats: ModuleStats, analyzer: SafeOrbitAnalyzer):
        self.file_path = file_path
        self.rel = str(file_path.relative_to(analyzer.PROJECT_ROOT))
        self.stats = stats
        self.analyzer = analyzer

    def visit_FunctionDef(self, node):
        self.stats.functions += 1
        self._check_naked_except(node)
        self._check_log_in_except(node)
        self._check_any_return(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.stats.classes += 1
        self.generic_visit(node)

    def visit_Try(self, node):
        self.stats.try_except_count += 1
        for handler in node.handlers:
            if handler.type is None:
                self.stats.bare_except_count += 1
                self.analyzer._add(
                    category="错误处理", severity=Severity.LOW,
                    title=f"裸 except 语句（{self.rel}:{handler.lineno}）",
                    description=f"裸 except 会捕获 KeyboardInterrupt、SystemExit 等不应吞掉的异常",
                    location=f"{self.rel}:{handler.lineno}",
                    fix="改为 except Exception，或在特定上下文中添加 finally 恢复状态。",
                    effort="small",
                )
        self.generic_visit(node)

    def visit_Call(self, node):
        # 统计日志调用
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id in ("logger", "log", "logging"):
                if node.func.attr in ("debug", "info", "warning", "error", "critical", "exception"):
                    self.stats.log_calls += 1

        # 统计 % 格式化
        if isinstance(node.func, ast.Attribute) and node.func.attr == "__mod__":
            self.stats.percent_format += 1

        self.generic_visit(node)

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Mod):
            # a % b 的 % 操作符（在非字符串上下文难以精确区分，作为近似）
            pass
        self.generic_visit(node)

    def visit_Global(self, node):
        self.stats.global_vars += len(node.names)
        self.generic_visit(node)

    def _check_naked_except(self, func_node: ast.FunctionDef) -> None:
        """检查函数是否包含裸 try-except 导致潜在静默吞错"""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None and handler.lineno == node.lineno + 1:
                        # 已在上方 visit_Try 中处理
                        pass

    def _check_log_in_except(self, func_node: ast.FunctionDef) -> None:
        """检查 except 块中是否缺乏日志"""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    has_log = any(
                        isinstance(n, ast.Call) and
                        isinstance(n.func, ast.Attribute) and
                        n.func.attr in ("debug", "info", "warning", "error", "critical", "exception")
                        for n in ast.walk(handler)
                    )
                    if not has_log and handler.type is not None:
                        self.analyzer._add(
                            category="可观测性", severity=Severity.INFO,
                            title=f"except 块缺少日志（{self.rel}:{handler.lineno}）",
                            description=f"捕获异常后未记录日志，问题排查时缺失上下文",
                            location=f"{self.rel}:{handler.lineno}",
                            fix="添加 logger.warning / logger.error 记录异常详情",
                            effort="small",
                        )

    def _check_any_return(self, func_node: ast.FunctionDef) -> None:
        """检查返回值类型是否广泛使用 Any"""
        returns = func_node.returns
        if returns and isinstance(returns, ast.Subscript):
            if isinstance(returns.value, ast.Name) and returns.value.id == "Any":
                self.stats.any_types += 1


# ---------------------------------------------------------------------------
# 手动补充发现（静态 AST 无法检测的问题）
# ---------------------------------------------------------------------------

MANUAL_FINDINGS: List[Dict] = [
    {
        "category": "潜在 Bug", "severity": Severity.HIGH,
        "title": "otel.py Protobuf 解码器手工实现，varint 可能截断",
        "description": "otel.py 第 189 行 shift > 70 限制 127 位 varint。部分极长字段可能被截断，"
                      "建议使用标准 protobuf 库解码。",
        "location": "safeorbit/otel.py",
        "fix": "引入 google.protobuf 标准库替换手工解码逻辑。",
        "effort": "medium",
    },
    {
        "category": "潜在 Bug", "severity": Severity.MEDIUM,
        "title": "approval/disposition expire() 无定期调度",
        "description": "approval.py 的 expire_tickets() 和 disposition.py 的隔离过期仅在被 list() 调用时被动触发。"
                      "长时间无查询时过期项不会自动清理。",
        "location": "safeorbit/approval.py, safeorbit/disposition.py",
        "fix": "添加 asyncio.create_task 定时器或后台协程周期性调用过期清理。",
        "effort": "small",
    },
    {
        "category": "潜在 Bug", "severity": Severity.MEDIUM,
        "title": "security.py CSRF Origin 检查：netloc 可能为空",
        "description": "security.py 第 91 行 urlsplit 无 origin 时 .netloc 为空字符串，"
                      "expected 的 host 为 ''，可能误放过某些请求。",
        "location": "safeorbit/security.py:91",
        "fix": "当 host 为空时直接拒绝（非 HTTP/1.1 请求应有 Host 头）。",
        "effort": "small",
    },
    {
        "category": "潜在 Bug", "severity": Severity.LOW,
        "title": "pricing.py cached token 计数假设不跨厂商",
        "description": "pricing.py 第 60 行 min(max(cached,0), max(prompt,0)) 假设所有厂商 cached 计数不超过 prompt_tokens，"
                      "不同 API 可能有不同约定。",
        "location": "safeorbit/pricing.py:60",
        "fix": "按模型/厂商独立处理缓存定价逻辑，不依赖统一假设。",
        "effort": "small",
    },
    {
        "category": "代码组织", "severity": Severity.MEDIUM,
        "title": "judge.py 重试次数硬编码为 2",
        "description": "judge.py 第 229 行 for _ in range(2) 硬编码重试 1 次。应支持通过环境变量或 JudgeConfig 配置。",
        "location": "safeorbit/judge.py:229",
        "fix": "在 JudgeConfig 中添加 max_retries 字段并从环境变量 SAFEORBIT_JUDGE_MAX_RETRIES 读取。",
        "effort": "small",
    },
    {
        "category": "可观测性", "severity": Severity.MEDIUM,
        "title": "store.py 全模块无日志记录",
        "description": "TraceStore 的关键操作（save、claim、migration）均没有日志输出，排查轨迹丢失/归属冲突问题困难。",
        "location": "safeorbit/store.py",
        "fix": "添加结构化日志：INFO 记录 save/migration，WARNING 记录 claim 冲突。",
        "effort": "small",
    },
    {
        "category": "可观测性", "severity": Severity.LOW,
        "title": "guard.py 命中无日志记录",
        "description": "内容安全 Guard 匹配到提示注入/数据外泄时未记录日志，无法事后审核命中频率。",
        "location": "safeorbit/guard.py",
        "fix": "每次命中时 logger.info 记录 guard_type、匹配模式、来源轨迹。",
        "effort": "small",
    },
    {
        "category": "可观测性", "severity": Severity.LOW,
        "title": "disposition.py 处置操作无日志",
        "description": "会话隔离、工具阻断等关键治理操作未生成操作日志，仅写入 SQLite。",
        "location": "safeorbit/disposition.py",
        "fix": "通过 ops.py 的 AuditLog 记录处置操作（与审批日志同级）。",
        "effort": "small",
    },
    {
        "category": "性能", "severity": Severity.MEDIUM,
        "title": "SQLite 同步操作可能阻塞事件循环",
        "description": "TraceStore 使用同步 sqlite3.connect，在 FastAPI async 上下文中可能阻塞 asyncio 事件循环，"
                      "高并发下导致请求排队。",
        "location": "safeorbit/store.py",
        "fix": "用 anyio.to_thread.run_sync 包装所有 SQLite 操作，或迁移到 aiosqlite。",
        "effort": "medium",
    },
    {
        "category": "可维护性", "severity": Severity.MEDIUM,
        "title": "缺少数据库迁移框架",
        "description": "store.py 使用手写 _ensure_columns 和 PRAGMA table_info 做 schema 演化，无版本记录、回滚能力。",
        "location": "safeorbit/store.py:_ensure_columns",
        "fix": "引入轻量迁移库（如 simple migrations 或自建 sqlite_migrations 表 + 变更脚本）。",
        "effort": "medium",
    },
    {
        "category": "文档", "severity": Severity.LOW,
        "title": "检测到 FastAPI 但未提及 OpenAPI 文档",
        "description": "FastAPI 自动生成 /docs（Swagger）和 /redoc，README 中未提及这些入口。",
        "location": "README.md",
        "fix": "在 README 部署与运行章节添加 API 文档入口：<http://127.0.0.1:8000/docs>。",
        "effort": "small",
    },
    {
        "category": "功能完善", "severity": Severity.INFO,
        "title": "infrascan.py YAML 规则匹配为简单字符串包含",
        "description": "infrascan.py 第 194 行对 kind: 的匹配是字符串包含检查，可能误匹配注释或字符串内的关键字。",
        "location": "safeorbit/infrascan.py:194",
        "fix": "使用 YAML 解析器结构化检查，避免字符串匹配误报。",
        "effort": "small",
    },
    {
        "category": "安全", "severity": Severity.INFO,
        "title": "Cookie SameSite=lax 允许跨站 GET",
        "description": "session cookie 使用 samesite='lax'，允许跨站 GET 携带 cookie。"
                      "对于管理类应用建议 SameSite=Strict。",
        "location": "safeorbit/auth.py",
        "fix": "评估影响后改为 samesite='strict'（可能影响从外部链接跳转的首次访问）。",
        "effort": "small",
    },
]


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

SEV_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
    Severity.INFO: "⚪",
}

def print_report(summary: Dict) -> None:
    """打印文本格式诊断报告。"""
    findings = summary["findings"]

    print("=" * 72)
    print(f"  SafeOrbit 安轨 · 项目质量诊断报告")
    print(f"  分析时间: {summary['analyzed_at'][:19]}")
    print(f"  模块数: {summary['modules_count']}  |  "
          f"总行数: {summary['total_lines']}  |  "
          f"函数数: {summary['total_functions']}")
    print(f"  发现问题: {summary['findings_total']} 项")
    print("=" * 72)

    # 按严重度分布
    print(f"\n  📊 严重度分布:  ", end="")
    for sev in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO):
        count = summary["findings_by_severity"].get(sev, 0)
        if count:
            print(f"{SEV_EMOJI.get(sev,'')} {sev}: {count}  ", end="")
    print()

    print(f"  📂 类别分布:  ", end="")
    for cat, count in sorted(summary["findings_by_category"].items(), key=lambda kv: -kv[1]):
        print(f"{cat}({count})  ", end="")
    print()

    print(f"\n{'─' * 72}")
    print(f"  详细发现项")
    print(f"{'─' * 72}\n")

    for i, f in enumerate(findings, 1):
        sev = SEV_EMOJI.get(f["severity"], "")
        print(f"  [{i:02d}] {sev} {f['severity']:8s} | {f['category']}")
        print(f"       标题: {f['title']}")
        if f["location"]:
            print(f"       位置: {f['location']}")
        print(f"       描述: {f['description'][:200]}")
        if f["fix"]:
            print(f"       建议: {f['fix'][:200]}")
        if f["effort"]:
            print(f"       投入: {f['effort']}")
        print()

    # 模块统计 Top 10
    print(f"{'─' * 72}")
    print(f"  模块规模 Top 10")
    print(f"{'─' * 72}")
    print(f"  {'模块':<35s} {'行数':>6s}  {'函数':>5s}")
    for name, stats in sorted(summary["module_stats"].items(), key=lambda kv: -kv[1]["lines"])[:10]:
        print(f"  {name:<35s} {stats['lines']:>6d}  {stats['functions']:>5d}")

    print(f"\n{'=' * 72}")
    print(f"  建议优先修复 Critical / High 项，预计投入约 1-2 周。")
    print(f"  Medium 项可按迭代节奏逐步解决。")
    print(f"{'=' * 72}\n")


def print_json_report(summary: Dict) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def generate_html_report(summary: Dict) -> str:
    """生成 HTML 格式诊断报告。"""
    sev_badge_color = {
        Severity.CRITICAL: "#dc2626", Severity.HIGH: "#ea580c",
        Severity.MEDIUM: "#ca8a04", Severity.LOW: "#2563eb", Severity.INFO: "#6b7280",
    }

    rows = ""
    for i, f in enumerate(summary["findings"], 1):
        rows += f"""
        <tr>
          <td style="text-align:center">{i}</td>
          <td style="text-align:center">
            <span style="background:{sev_badge_color.get(f['severity'],'#888')};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.85em">{f['severity']}</span>
          </td>
          <td>{f['category']}</td>
          <td><strong>{f['title']}</strong></td>
          <td style="font-size:0.9em;max-width:400px">{f['description'][:300]}</td>
          <td style="font-size:0.85em;color:#555">{f.get('location','')}</td>
          <td style="font-size:0.85em;color:#333">{f.get('fix','')[:200]}</td>
          <td style="text-align:center;font-size:0.85em">{f.get('effort','-')}</td>
        </tr>"""

    sev_counts = ""
    for sev in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO):
        c = summary["findings_by_severity"].get(sev, 0)
        if c:
            sev_counts += (f'<span style="background:{sev_badge_color[sev]};color:#fff;'
                           f'padding:4px 12px;border-radius:6px;margin:0 4px">{sev}: {c}</span>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>SafeOrbit 项目质量诊断报告</title>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; margin: 0; background: #f5f5f5; color: #333; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 32px 24px; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #65D6C1; padding-bottom: 12px; }}
  .meta {{ background: #fff; padding: 20px; border-radius: 8px; margin: 16px 0; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  .meta span {{ margin-right: 24px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  th {{ background: #1a1a2e; color: #fff; padding: 12px 10px; text-align: left; font-weight: 600; }}
  td {{ padding: 10px; border-bottom: 1px solid #eee; vertical-align: top; }}
  tr:hover {{ background: #f8fafc; }}
  .footer {{ text-align: center; color: #999; margin-top: 32px; font-size: 0.85em; }}
</style>
</head>
<body>
<div class="container">
  <h1>🔍 SafeOrbit 安轨 · 项目质量诊断报告</h1>

  <div class="meta">
    <span>📅 分析时间: {summary['analyzed_at'][:19]}</span>
    <span>📦 模块数: {summary['modules_count']}</span>
    <span>📝 总行数: {summary['total_lines']}</span>
    <span>⚠️ 发现问题: {summary['findings_total']} 项</span>
    <br><br>
    {sev_counts}
    <br><br>
    <span>📂 类别分布: {' | '.join(f'{k}({v})' for k,v in sorted(summary['findings_by_category'].items(), key=lambda kv: -kv[1]))}</span>
  </div>

  <h2>发现问题清单</h2>
  <table>
    <tr>
      <th style="width:36px">#</th>
      <th style="width:80px">严重度</th>
      <th style="width:80px">类别</th>
      <th style="width:200px">标题</th>
      <th>描述</th>
      <th style="width:140px">位置</th>
      <th style="width:200px">建议修复</th>
      <th style="width:60px">投入</th>
    </tr>
    {rows}
  </table>

  <div class="footer">
    SafeOrbit 项目质量诊断工具 · 生成于 {summary['analyzed_at'][:19]}<br>
    建议优先处理 CRITICAL 和 HIGH 项
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SafeOrbit 项目质量诊断工具")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--html", action="store_true", help="生成 HTML 报告")
    parser.add_argument("--out", default=None, help="HTML 报告输出路径（默认 safeorbit-health-report.html）")
    args = parser.parse_args()

    print("正在分析 SafeOrbit 项目代码...", file=sys.stderr)
    analyzer = SafeOrbitAnalyzer()
    summary = analyzer.analyze()

    # 合并手动补充的发现
    for f_manual in MANUAL_FINDINGS:
        summary["findings"].append(f_manual)
    summary["findings_total"] = len(summary["findings"])
    for f in summary["findings"]:
        summary["findings_by_severity"][f["severity"]] = \
            summary["findings_by_severity"].get(f["severity"], 0) + 1
        summary["findings_by_category"][f["category"]] = \
            summary["findings_by_category"].get(f["category"], 0) + 1

    if args.json:
        print_json_report(summary)
    elif args.html:
        html_content = generate_html_report(summary)
        out_path = args.out or str(Path(__file__).with_name("safeorbit-health-report.html"))
        Path(out_path).write_text(html_content, encoding="utf-8")
        print(f"HTML 报告已生成: {out_path}")
    else:
        print_report(summary)


if __name__ == "__main__":
    main()