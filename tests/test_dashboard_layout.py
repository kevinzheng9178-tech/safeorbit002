"""控制台展示结构与业务挂载点契约。"""

import re
import shutil
import subprocess
from collections import Counter
from html.parser import HTMLParser

import pytest

from safeorbit.dashboard import DASHBOARD_HTML


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.nodes.append((tag, attrs, tuple(self.stack)))
        if tag not in {"meta", "input", "br", "hr", "link"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1][0] == tag, tag
        self.stack.pop()


def dashboard_nodes():
    parser = DashboardParser()
    parser.feed(DASHBOARD_HTML)
    assert not parser.stack
    return parser.nodes


def test_dashboard_ids_and_script_mounts():
    nodes = dashboard_nodes()
    ids = [attrs["id"] for _, attrs, _ in nodes if "id" in attrs]
    assert all(count == 1 for count in Counter(ids).values())
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    references = set(re.findall(r'getElementById\([\"\x27]([^\"\x27]+)[\"\x27]\)', script))
    references.update("ms-" + name for name in re.findall(r"\bms\('([^']+)'\)", script))
    # 审批角标由侧边栏动态渲染，不属于静态 DOM
    references -= {"side-approval-count"}
    assert references <= set(ids)


def test_dashboard_sidebar_navigation_contract():
    """Linear 式单侧边栏：固定模块 + 任务组 + 底部身份，主区独立滚动。"""

    nodes = dashboard_nodes()
    modules = [attrs["data-module"] for _, attrs, _ in nodes if "data-module" in attrs]
    assert modules == ["guide", "agents", "models", "account"]
    panels = [attrs["id"] for _, attrs, _ in nodes if "panel" in attrs.get("class", "").split()]
    assert set(panels) == {
        "panel-guide", "panel-overview", "panel-traces", "panel-audit", "panel-policies",
        "panel-dispositions", "panel-codeaudit", "panel-redteam", "panel-agents",
        "panel-settings", "panel-agent", "panel-task", "panel-account",
    }
    assert sum(
        "active" in attrs.get("class", "").split()
        for _, attrs, _ in nodes
        if attrs.get("id") in panels
    ) == 1
    for name in ["side", "side-body", "side-groups", "side-collapse", "side-resizer",
                 "side-scrim", "side-user", "side-approval-count",
                 "side-agents-toggle", "side-guide-toggle",
                 "l3-body", "crumb", "tb-nav", "tb-views", "drawer", "drawer-body",
                 "palette", "palette-input"]:
        assert 'id="%s"' % name in DASHBOARD_HTML
    # 旧的 L1 图标栏 / L2 导航已合并进单侧边栏
    assert 'class="l1"' not in DASHBOARD_HTML
    assert 'class="l2"' not in DASHBOARD_HTML
    # 三个固定模块：使用导航 / Agent 管理 / 系统设置
    for label in ["使用导航", "Agent 管理", "系统设置"]:
        assert label in DASHBOARD_HTML
    # 四个任务组块
    for label in ["现有 Agent", "Agent 审计", "代码审计", "红队检测"]:
        assert label in DASHBOARD_HTML
    # 侧边栏样式：可拖拽宽度、窄屏浮层、抽屉 480px
    assert "--sidew" in DASHBOARD_HTML
    assert "width: 480px" in DASHBOARD_HTML
    assert "@media (max-width: 1023px)" in DASHBOARD_HTML
    assert ".l3-body .page-heading { display: none; }" in DASHBOARD_HTML
    # 交互与持久化
    assert "safeorbit.nav.v2" in DASHBOARD_HTML
    assert "safeorbit.tasks.v1" in DASHBOARD_HTML
    assert 'event.key.toLowerCase() === "k"' in DASHBOARD_HTML
    for fn in [
        "function renderSide(", "function renderAgentPage(", "function renderTaskPage(",
        "function renderAccount(", "function openAgentPage(", "function openTaskPage(",
        "function runAgentAudit(", "function runAgentCodeAudit(", "function runAgentRedteam(",
        "function switchAccount(", "function registerAccount(", "function scrollAgentsSection(",
        "function showRunDetail(",
    ]:
        assert fn in DASHBOARD_HTML
    # Agent 工作台两个入口：任务总计 / 检查记录（记录按任务类型分组）
    for name in ["agent-tab-total", "agent-tab-records"]:
        assert 'id="%s"' % name in DASHBOARD_HTML
    assert "data-agent-row" in DASHBOARD_HTML
    assert "data-task-row" in DASHBOARD_HTML
    assert "data-agent-delete" in DASHBOARD_HTML
    assert 'method: "DELETE"' in DASHBOARD_HTML
    # 四个任务组块与顶部固定块一样可展开 / 收起，状态本地持久化
    assert "data-group-toggle" in DASHBOARD_HTML
    assert ".side-group-toggle" in DASHBOARD_HTML
    assert "side-group-body" in DASHBOARD_HTML
    assert "navState.groups" in DASHBOARD_HTML
    assert "groups: navState.groups" in DASHBOARD_HTML
    assert "GROUP_ICONS" in DASHBOARD_HTML
    assert "GROUP_ICONS[key]" in DASHBOARD_HTML
    for key in ["agents", "agent_audit", "code_audit", "redteam"]:
        assert key in DASHBOARD_HTML
    # 设置：身份切换、注册账号、登出
    for name in ["account-switch", "account-register", "account-logout", "account-current"]:
        assert 'id="%s"' % name in DASHBOARD_HTML
    # 导航页与智能体接入入口仍在
    assert 'id="panel-guide"' in DASHBOARD_HTML
    assert 'id="guide-next"' in DASHBOARD_HTML
    assert "data-guide-go" in DASHBOARD_HTML
    assert "function renderGuide" in DASHBOARD_HTML
    assert "data-agents-section" in DASHBOARD_HTML
    for name in ["guide-primary", "guide-ring-value", "guide-progress-count", "guide-copy-demo"]:
        assert 'id="%s"' % name in DASHBOARD_HTML


def test_dashboard_summary_panel_contract():
    nodes = dashboard_nodes()
    ids = {attrs["id"] for _, attrs, _ in nodes if "id" in attrs}
    for name in [
        "summary", "summary-scope", "summary-refresh", "summary-export", "summary-latest",
        "summary-msg", "summary-verdicts", "summary-agents", "summary-trend", "summary-risks",
    ]:
        assert name in ids
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "function refreshSummary(" in script
    assert "/api/traces/overview" in script
    assert 'can("auditor")' in script


def test_dashboard_tables_scroll_locally_and_security_stays_hidden():
    nodes = dashboard_nodes()
    for tag, _, parents in nodes:
        if tag == "table":
            assert "table-scroll" in parents[-1][1].get("class", "").split()
            assert parents[-1][1].get("tabindex") == "0"
    by_id = {attrs["id"]: attrs for _, attrs, _ in nodes if "id" in attrs}
    for name in ["model-settings", "ms-editor", "agent-detail", "agent-secret-box", "agent-model-field"]:
        assert "hidden" in by_id[name]
    assert "disabled" in by_id["agent-model"]
    assert "readonly" in by_id["agent-secret"]
    assert by_id["ms-key"]["type"] == "password"
    assert "[hidden] { display: none !important; }" in DASHBOARD_HTML
    assert ":focus-visible" in DASHBOARD_HTML
    assert "@media (max-width: 767px)" in DASHBOARD_HTML
    assert "prefers-reduced-motion: reduce" in DASHBOARD_HTML


def test_dashboard_details_start_collapsed():
    """所有可折叠区块初始都必须收起，空数据时也一样。"""

    assert not re.search(r"<details[^>]*\bopen\b", DASHBOARD_HTML)
    for name in [
        "trace-filters", "summary", "recent-card", "alerts-card", "policy-form",
        "policy-list-card", "approval-card", "runtime-isolated-card",
        "runtime-tools-card", "manual-disposition-card", "code-result-card",
        "extended-audit-card", "data-governance-card",
        "model-settings", "agent-management-card", "version-card",
    ]:
        assert 'id="%s"' % name in DASHBOARD_HTML


def test_code_audit_drag_and_drop_upload_contract():
    """代码检查页提供拖拽上传区，支持 zip、文件夹、多文件，并走上传接口。"""

    for name in ["code-dropzone", "code-file", "code-folder", "code-folder-btn"]:
        assert 'id="%s"' % name in DASHBOARD_HTML
    assert 'class="dropzone" id="code-dropzone"' in DASHBOARD_HTML
    assert "webkitGetAsEntry" in DASHBOARD_HTML
    assert "webkitdirectory" in DASHBOARD_HTML
    assert "/api/codeaudit/upload" in DASHBOARD_HTML
    assert "dragover" in DASHBOARD_HTML


def test_dashboard_ambient_audit_background():
    """控制台背景为静态审计底纹：网格、光晕、光带保留，但不做全屏持续重绘。"""

    assert '<div class="bg-ambient" aria-hidden="true">' in DASHBOARD_HTML
    for token in ("bg-halo", "bg-scan"):
        assert token in DASHBOARD_HTML
    # 静态化后不再声明漂移/扫描/呼吸动画，避免非整数 DPI 下全屏合成层黑屏
    assert "@keyframes bgDrift" not in DASHBOARD_HTML
    assert "@keyframes bgScan" not in DASHBOARD_HTML
    assert "@keyframes bgHalo" not in DASHBOARD_HTML
    assert "pointer-events: none" in DASHBOARD_HTML
    assert "@media (prefers-reduced-motion: reduce)" in DASHBOARD_HTML


def test_realtime_alerts_live_in_overview():
    """实时提醒归属总览；运行记录只保留列表与详情，避免跨模块堆叠。"""

    assert DASHBOARD_HTML.index('id="alerts-card"') < DASHBOARD_HTML.index('id="panel-traces"')
    assert 'id="panel-overview"' in DASHBOARD_HTML
    assert "alertsCard.open = true" in DASHBOARD_HTML


def test_dashboard_landing_intro_contract():
    """初始介绍页：默认可见，进入控制台后隐藏，登录失败回到介绍页。"""

    nodes = dashboard_nodes()
    by_id = {attrs["id"]: attrs for _, attrs, _ in nodes if "id" in attrs}
    for name in [
        "landing", "landing-enter", "landing-enter-cta", "landing-capabilities-jump",
        "landing-capabilities", "landing-status", "landing-status-text", "login-back",
        "landing-canvas",
    ]:
        assert name in by_id
    assert "hidden" not in by_id["landing"]
    assert "show" not in by_id["login"].get("class", "")
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "function showLanding(" in script
    assert "document.getElementById(\"landing\").hidden = true" in script
    assert "loadLandingHealth()" in script
    assert 'document.getElementById("login-back").addEventListener("click", showLanding)' in script
    # 动效必须尊重 reduced-motion，且离开介绍页后停止动画循环
    assert "prefers-reduced-motion: reduce" in script
    assert "function stopLandingMotion(" in script
    assert "setupLandingReveal()" in script


def test_dashboard_audit_views_and_hotl_contract():
    """五视图与 HOTL 指标必须同时存在于页面与脚本中。"""

    nodes = dashboard_nodes()
    by_id = {attrs["id"]: attrs for _, attrs, _ in nodes if "id" in attrs}
    for name in [
        "replay-tabs", "view-timeline", "view-resources", "view-permissions",
        "view-decisions", "view-impact", "resource-grid", "permission-list",
        "decision-list", "impact-banner", "impact-list",
        "hotl-card", "hotl-escalation", "hotl-rejection", "hotl-drift",
        "hotl-decision", "hotl-cost", "hotl-note",
    ]:
        assert name in by_id
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    for marker in (
        "/audit-views", "/api/audit/metrics", "function switchReplayView(",
        "function renderAuditViews(", "approvalRisk(", "data-note-for",
    ):
        assert marker in script
    assert 'id="view-timeline"' in DASHBOARD_HTML
    assert 'id="hotl-card"' in DASHBOARD_HTML


def test_dashboard_review_and_judge_prompt_contract():
    """评审速览、三分钟演示与裁判提示词展示必须存在。"""

    nodes = dashboard_nodes()
    ids = {attrs["id"] for _, attrs, _ in nodes if "id" in attrs}
    for name in ["judge-prompt-fold", "judge-prompt-meta", "judge-prompt-text", "judge-prompt-guards"]:
        assert name in ids
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    assert "/api/judge/prompt" in script
    assert "loadJudgePrompt" in script
    assert 'class="review-grid"' in DASHBOARD_HTML
    assert "三分钟演示" in DASHBOARD_HTML


def test_dashboard_inline_script_is_valid_javascript(tmp_path):
    """Python 三引号里的转义错误会让整段脚本失效，这里用 node 做语法校验。"""

    node = shutil.which("node")
    if not node:
        pytest.skip("node 未安装，跳过脚本语法校验")
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    target = tmp_path / "dashboard.js"
    target.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(target)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
