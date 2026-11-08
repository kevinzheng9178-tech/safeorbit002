"""使用临时 SQLite 数据库验证轨迹归属和会话隔离。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3
from threading import Barrier

import pytest

from safeorbit.disposition import DispositionStore
from safeorbit.models import Trajectory
from safeorbit.pricing import load_pricing
from safeorbit.report import build_report
from safeorbit.store import TraceStore


def _save(store, trace_id, agent_id, cost=1.0):
    now = datetime.now(timezone.utc)
    trajectory = Trajectory(
        trace_id=trace_id, agent_id=agent_id, session_id="shared",
        started_at=now, received_at=now,
    )
    report = build_report(trajectory, [], load_pricing())
    report.cost.total_usd = cost
    store.save(trajectory, report, "report")


def test_concurrent_trace_claim_has_one_winner(tmp_path):
    path = str(tmp_path / "traces.db")
    stores = [TraceStore(path), TraceStore(path)]
    barrier = Barrier(2)

    def compete(index):
        barrier.wait()
        try:
            stores[index].claim("race", "agent-%d" % index)
            return index
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(compete, range(2)))
    winners = [index for index in results if index is not None]
    assert len(winners) == 1
    winner = winners[0]
    stores[winner].claim("race", "agent-%d" % winner)
    with pytest.raises(ValueError):
        _save(stores[0], "race", "agent-%d" % (1 - winner))
    _save(stores[0], "race", "agent-%d" % winner)


def test_concurrent_save_cannot_overwrite_other_agent(tmp_path):
    path = str(tmp_path / "save-race.db")
    stores = [TraceStore(path), TraceStore(path)]
    barrier = Barrier(2)

    def compete(index):
        barrier.wait()
        try:
            _save(stores[index], "race", "agent-%d" % index)
            return index
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(compete, range(2)))
    winners = [index for index in results if index is not None]
    assert len(winners) == 1
    assert stores[0].get("race")["agent_id"] == "agent-%d" % winners[0]


def test_claim_many_rolls_back_all_bindings(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    store.claim("taken", "a")
    with pytest.raises(ValueError):
        store.claim_many([("fresh", "a"), ("taken", "b")])
    store.claim("fresh", "b")
    with pytest.raises(ValueError):
        store.claim_many([("duplicate", "a"), ("duplicate", "b")])
    store.claim("duplicate", "b")


def test_save_checks_existing_owner_and_cost_is_scoped(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    _save(store, "a", "agent-a", 2.0)
    _save(store, "b", "agent-b", 7.0)
    with pytest.raises(ValueError):
        _save(store, "a", "agent-b", 99.0)
    assert store.get("a")["agent_id"] == "agent-a"
    assert store.session_cost("shared", "agent-a") == 2.0
    assert store.session_cost("shared", "agent-b") == 7.0
    assert store.session_cost(None, "agent-a") == 0.0
    with pytest.raises(TypeError):
        store.session_cost("shared")
    with pytest.raises(ValueError):
        store.session_cost("shared", "*")


def test_trace_migration_preserves_known_and_denies_unknown_owners(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE traces (trace_id TEXT PRIMARY KEY, agent_id TEXT)")
        connection.executemany(
            "INSERT INTO traces VALUES (?, ?)",
            [("known", "a"), ("unknown", None), ("empty", ""), ("wildcard", "*")],
        )
    for _ in range(2):
        store = TraceStore(path)
        store.claim("known", "a")
        with pytest.raises(ValueError):
            store.claim("known", "b")
        for trace_id in ("unknown", "empty", "wildcard"):
            with pytest.raises(ValueError):
                store.claim(trace_id, "a")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 4
        assert connection.execute("SELECT * FROM trace_owners").fetchall() == [("known", "a")]
        assert connection.execute("SELECT * FROM trace_schema_migrations").fetchall() == [(1,)]


def _isolate(store, agent_id):
    proposal = store.propose(
        agent_id=agent_id, action="isolate", reason="risk", session_id="shared",
    )
    store.decide(proposal.disposition_id, approved=True, decided_by="auditor")
    store.execute(proposal.disposition_id, mode="enforce", executed_by="operator")


def test_same_session_isolation_and_release_are_agent_scoped(tmp_path):
    store = DispositionStore(str(tmp_path / "disposition.db"))
    _isolate(store, "a")
    assert store.is_session_isolated("shared", "b") is None
    assert not store.release_isolation("shared", "b")
    _isolate(store, "b")
    assert store.is_session_isolated("shared", "a")
    assert store.is_session_isolated("shared", "b")
    assert store.release_isolation("shared", "a")
    assert store.is_session_isolated("shared", "a") is None
    assert store.is_session_isolated("shared", "b")
    with pytest.raises(TypeError):
        store.is_session_isolated("shared")
    with pytest.raises(TypeError):
        store.release_isolation("shared")
    with pytest.raises(ValueError):
        _isolate(store, "*")
    with pytest.raises(ValueError):
        store.release_isolation("shared", "")


def _legacy_isolation(path, agent_id):
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            "CREATE TABLE isolated_sessions (session_id TEXT PRIMARY KEY, agent_id TEXT, "
            "disposition_id TEXT, reason TEXT, until TEXT, created_at TEXT, active INTEGER DEFAULT 1)"
        )
        connection.execute(
            "INSERT INTO isolated_sessions VALUES ('shared', ?, 'old-id', 'old reason', NULL, 'old-time', 1)",
            (agent_id,),
        )


def test_disposition_legacy_migration_is_idempotent_and_keeps_data(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_isolation(path, "a")
    for _ in range(2):
        store = DispositionStore(str(path))
        row = store.is_session_isolated("shared", "a")
        assert row["disposition_id"] == "old-id"
        assert row["reason"] == "old reason"
        assert row["created_at"] == "old-time"
        assert store.is_session_isolated("shared", "b") is None
    _isolate(store, "b")
    assert store.is_session_isolated("shared", "a")
    with sqlite3.connect(str(path)) as connection:
        assert connection.execute("SELECT * FROM disposition_schema_migrations").fetchall() == [(1,)]


@pytest.mark.parametrize("agent_id", [None, "", " ", "*"])
def test_ambiguous_legacy_isolation_stops_without_changing_data(tmp_path, agent_id):
    path = tmp_path / "ambiguous.db"
    _legacy_isolation(path, agent_id)
    for _ in range(2):
        with pytest.raises(ValueError, match="人工处理"):
            DispositionStore(str(path))
        with sqlite3.connect(str(path)) as connection:
            assert connection.execute("SELECT * FROM isolated_sessions").fetchall() == [
                ("shared", agent_id, "old-id", "old reason", None, "old-time", 1)
            ]
            columns = connection.execute("PRAGMA table_info(isolated_sessions)").fetchall()
            assert [row[1] for row in columns if row[5]] == ["session_id"]
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'disposition_schema_migrations'"
            ).fetchone() is None
