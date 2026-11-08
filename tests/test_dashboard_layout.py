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
    assert references <= set(ids)


def test_dashboard_three_layer_navigation_contract():
    """L1 图标栏 / L2 导航 / L3 主内容 + 工具条 + 抽屉。"""

    nodes = dashboard_nodes()
    modules = [attrs["data-module"] for _, attrs, _ in nodes if "data-module" in attrs]
    assert modules == ["guide", "overview", "runs", "approvals", "policies", "agents", "settings"]
    panels = [attrs["id"] for _, attrs, _ in nodes if "panel" in attrs.get("class", "").split()]
    assert set(panels) == {
        "panel-guide", "panel-overview", "panel-traces", "panel-policies",
        "panel-dispositions", "panel-codeaudit", "panel-agents", "panel-settings",
    }
    assert sum(
        "active" in attrs.get("class", "").split()
        for _, attrs, _ in nodes
        if attrs.get("id") in panels
    ) == 1
    for name in ["l1", "l2", "l2-body", "l2-collapse", "l2-resizer", "l3-body",
                 "crumb", "tb-nav", "tb-views", "drawer", "drawer-body", "palette", "palette-input"]:
        assert 'id="%s"' % name in DASHBOARD_HTML
    # 结构要求：无页面级大标题，L2 归属 L1，L3 内嵌 44px 工具条
    assert ".l3-body .page-heading { display: none; }" in DASHBOARD_HTML
    assert "--l2w" in DASHBOARD_HTML
    assert "width: 480px" in DASHBOARD_HTML
    assert "@media (max-width: 1279px)" in DASHBOARD_HTML
    # 交互与持久化
    assert "safeorbit.nav.v1" in DASHBOARD_HTML
    assert 'event.key.toLowerCase() === "k"' in DASHBOARD_HTML
    assert "function renderL2Runs" in DASHBOARD_HTML
    assert "function renderL2Approvals" in DASHBOARD_HTML
    assert "function showRunDetail" in DASHBOARD_HTML
    assert "data-run-open" in DASHBOARD_HTML
    assert "data-approval-id" in DASHBOARD_HTML
    # 导航页与智能体接入入口
    assert 'id="panel-guide"' in DASHBOARD_HTML
    assert 'id="guide-next"' in DASHBOARD_HTML
    assert "data-guide-go" in DASHBOARD_HTML
    assert "function renderGuide" in DASHBOARD_HTML
    assert "data-agents-section" in DASHBOARD_HTML
    # 总览 / 使用导航为整页布局，不占二级栏
    assert 'guide: {label: "使用导航", panel: "guide", full: true}' in DASHBOARD_HTML
    assert "meta.full" in DASHBOARD_HTML
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
        "model-catalog-card", "model-settings", "agent-management-card", "version-card",
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
    """控制台背景为动态审计底纹：网格漂移、扫描光束、呼吸光晕，且不拦截交互。"""

    assert '<div class="bg-ambient" aria-hidden="true">' in DASHBOARD_HTML
    for token in ("bg-halo", "bg-scan", "@keyframes bgDrift", "@keyframes bgScan", "@keyframes bgHalo"):
        assert token in DASHBOARD_HTML
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
