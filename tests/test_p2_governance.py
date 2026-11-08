"""P2 测试：账号、策略、处置闭环与运行时隔离。"""

from __future__ import annotations

from pathlib import Path

import pytest

from safeorbit.auth import AuthError, AuthManager, AuthStore, role_at_least
from safeorbit.disposition import DispositionStore
from safeorbit.policy import PolicyStore
from safeorbit.rules import AuditContext, default_rules


# --------------------------------------------------------------------------
# 账号与会话
# --------------------------------------------------------------------------
def test_bootstrap_admin_and_login(tmp_path: Path):
    store = AuthStore(str(tmp_path / "auth.db"))
    admin = store.bootstrap_admin("admin", "Safe-orbit-123!")
    assert admin is not None
    assert admin.role == "admin"
    assert store.bootstrap_admin("admin", "Safe-orbit-123!") is None

    assert store.verify_credentials("admin", "Safe-orbit-123!").role == "admin"
    assert store.verify_credentials("admin", "wrong") is None
    assert role_at_least("admin", "auditor")
    assert not role_at_least("viewer", "auditor")


def test_user_creation_validation_and_disable(tmp_path: Path):
    store = AuthStore(str(tmp_path / "auth.db"))
    store.create_user("alice", "Password-test-123!", role="auditor")
    assert store.get_user("alice").role == "auditor"

    with pytest.raises(AuthError):
        store.create_user("alice", "Password-test-123!", role="viewer")
    with pytest.raises(AuthError):
        store.create_user("bob", "123", role="viewer")
    with pytest.raises(AuthError):
        store.create_user("carol", "Password-test-123!", role="root")

    store.set_disabled("alice", True)
    assert store.verify_credentials("alice", "Password-test-123!") is None


def test_session_lifecycle(tmp_path: Path):
    store = AuthStore(str(tmp_path / "auth.db"))
    store.create_user("bob", "Password-test-123!", role="operator")
    token = store.create_session("bob")
    assert store.resolve_session(token).username == "bob"
    store.delete_session(token)
    assert store.resolve_session(token) is None

    expired = store.create_session("bob", ttl_hours=-1)
    assert store.resolve_session(expired) is None


def test_auth_manager_local_provider(tmp_path: Path):
    store = AuthStore(str(tmp_path / "auth.db"))
    store.bootstrap_admin("root", "Root-password-123!")
    manager = AuthManager(store)
    assert manager.bootstrap_note == ""

    user = manager.login("root", "Root-password-123!")
    token = manager.issue_session(user)
    assert manager.authenticate_session(token).role == "admin"
    with pytest.raises(AuthError):
        manager.login("root", "bad")
    with pytest.raises(AuthError):
        manager.sso_login({"code": "x"})
    assert any(provider["name"] == "sso" for provider in manager.provider_status())


# --------------------------------------------------------------------------
# 策略
# --------------------------------------------------------------------------
def test_policy_resolution_and_rule_selection(tmp_path: Path):
    store = PolicyStore(str(tmp_path / "policy.db"))
    default = store.upsert(
        agent_id="*",
        name="默认",
        mode="shadow",
        budget_usd=0.01,
        allowed_tools=["get_order"],
        loop_threshold=2,
    )
    specific = store.upsert(
        agent_id="ecs-cs-agent",
        name="客服",
        mode="enforce",
        budget_usd=0.0001,
        allowed_tools=["get_order", "send_message"],
        write_amount_threshold=1000,
        enabled_rules=["pii_leak", "tool_not_allowlisted"],
    )

    assert store.resolve("ecs-cs-agent").policy_id == specific.policy_id
    assert store.resolve("other-agent").policy_id == default.policy_id

    context = specific.to_context()
    assert context.budget_usd == 0.0001
    assert context.allowed_tools == {"get_order", "send_message"}
    assert context.write_amount_threshold == 1000

    selected = specific.select_rules(default_rules())
    assert {rule.id for rule in selected} == {"pii_leak", "tool_not_allowlisted"}
    assert len(default.select_rules(default_rules())) == len(default_rules())

    assert store.delete(specific.policy_id)
    assert store.resolve("ecs-cs-agent").policy_id == default.policy_id


def test_policy_rejects_unknown_mode(tmp_path: Path):
    store = PolicyStore(str(tmp_path / "policy.db"))
    with pytest.raises(ValueError):
        store.upsert(agent_id="a", mode="monitor")


def test_default_policy_is_created(tmp_path: Path):
    store = PolicyStore(str(tmp_path / "policy.db"))
    policy = store.ensure_default()
    assert policy.agent_id == "*"
    assert store.resolve("anything").policy_id == policy.policy_id


# --------------------------------------------------------------------------
# 处置闭环
# --------------------------------------------------------------------------
def test_disposition_shadow_execution_does_not_block(tmp_path: Path):
    store = DispositionStore(str(tmp_path / "disp.db"))
    proposal = store.propose(
        agent_id="agent-a",
        action="isolate",
        reason="高风险",
        session_id="sess-1",
        created_mode="shadow",
    )
    decided = store.decide(proposal.disposition_id, approved=True, decided_by="auditor")
    assert decided.status == "approved"

    executed = store.execute(
        proposal.disposition_id, mode="shadow", executed_by="operator"
    )
    assert executed.status == "executed"
    assert "影子模式" in executed.execution_note
    assert store.is_session_isolated("sess-1", "agent-a") is None


def test_disposition_enforce_isolates_and_blocks_tools(tmp_path: Path):
    store = DispositionStore(str(tmp_path / "disp.db"))
    isolate = store.propose(
        agent_id="agent-a",
        action="isolate",
        reason="严重风险",
        session_id="sess-2",
        created_mode="enforce",
    )
    store.decide(isolate.disposition_id, approved=True, decided_by="auditor")
    store.execute(isolate.disposition_id, mode="enforce", executed_by="operator")
    assert store.is_session_isolated("sess-2", "agent-a") is not None
    assert store.release_isolation("sess-2", "agent-a")
    assert store.is_session_isolated("sess-2", "agent-a") is None

    block = store.propose(
        agent_id="agent-a",
        action="block_tool",
        target="refund",
        reason="越权写操作",
        created_mode="enforce",
    )
    store.decide(block.disposition_id, approved=True, decided_by="auditor")
    store.execute(block.disposition_id, mode="enforce", executed_by="operator")
    assert store.is_tool_blocked("agent-a", "refund") is not None
    assert store.is_tool_blocked("agent-b", "refund") is None
    assert store.release_tool("agent-a", "refund")


def test_disposition_state_guards(tmp_path: Path):
    store = DispositionStore(str(tmp_path / "disp.db"))
    proposal = store.propose(agent_id="a", action="notify", reason="提醒")
    with pytest.raises(ValueError):
        store.execute(proposal.disposition_id, mode="enforce", executed_by="op")

    store.decide(proposal.disposition_id, approved=False, decided_by="auditor")
    with pytest.raises(ValueError):
        store.decide(proposal.disposition_id, approved=True, decided_by="auditor")


def test_auto_propose_deduplicates_by_trace(tmp_path: Path):
    store = DispositionStore(str(tmp_path / "disp.db"))
    hits = [
        {
            "rule_id": "tool_write_unconfirmed",
            "severity": "critical",
            "message": "写操作未确认",
        }
    ]
    proposal = store.auto_propose_from_report(
        trace_id="trace-1",
        agent_id="agent-a",
        session_id="sess-3",
        verdict="fail",
        hits=hits,
        mode="shadow",
    )
    assert proposal is not None
    assert proposal.action == "isolate"
    assert proposal.session_id == "sess-3"

    assert store.auto_propose_from_report(
        trace_id="trace-1",
        agent_id="agent-a",
        session_id="sess-3",
        verdict="fail",
        hits=hits,
        mode="shadow",
    ) is None
    assert store.has_for_trace("trace-1")

    assert store.auto_propose_from_report(
        trace_id="trace-2",
        agent_id="agent-a",
        session_id=None,
        verdict="pass",
        hits=[],
        mode="shadow",
    ) is None


def test_audit_context_defaults():
    context = AuditContext()
    assert context.loop_threshold == 3
    assert context.allowed_tools is None
