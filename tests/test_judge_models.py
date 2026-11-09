import asyncio
import json

import httpx
import pytest
import yaml

from safeorbit import cli, judge
from safeorbit.importers import load_trajectory
from safeorbit.judge import JudgeConfig, judge_trajectory
from safeorbit.model_registry import ModelRegistry
from safeorbit.models import Dimension, Severity
from safeorbit.normalize import normalize
from safeorbit.pricing import load_pricing


SECRET = "test-judge-secret-never-echo"
UPSTREAM_SECRET = "test-gateway-secret-never-use"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in (
        "SAFEORBIT_MODELS_CONFIG", "SAFEORBIT_JUDGE_PROFILE", "SAFEORBIT_JUDGE_API_KEY",
        "SAFEORBIT_UPSTREAM_API_KEY", "SAFEORBIT_UPSTREAM_BASE_URL", "SAFEORBIT_UPSTREAM_MODEL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "OPENAI_API_KEY",
        "TEST_JUDGE_KEY", "TEST_GATEWAY_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def registry_path(tmp_path):
    data = {
        "version": 1,
        "default_gateway": "chat",
        "judge_profile": "review",
        "profiles": [
            {"id": "chat", "provider": "openai", "model": "gateway-model",
             "api_key_env": "TEST_GATEWAY_KEY"},
            {"id": "review", "provider": "custom", "model": "independent-judge",
             "base_url": "https://judge.example/v1", "api_key_env": "TEST_JUDGE_KEY",
             "gateway": False, "judge": True, "supports_json_object": False},
        ],
    }
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def config(registry_path, monkeypatch):
    monkeypatch.setenv("TEST_JUDGE_KEY", SECRET)
    monkeypatch.setenv("TEST_GATEWAY_KEY", UPSTREAM_SECRET)
    cfg = JudgeConfig.from_registry(ModelRegistry.load(registry_path))
    # 测试固定为“失败后重试一次”，线上默认值可独立调整
    cfg.max_retries = 1
    return cfg


@pytest.fixture
def trajectory_path(tmp_path):
    path = tmp_path / "trajectory.json"
    path.write_text(json.dumps({
        "schema_version": "1.0", "trace_id": "judge-test", "agent_id": "test-agent",
        "started_at": "2026-11-03T10:00:00+08:00",
        "spans": [{"span_id": "s1", "kind": "agent", "name": "task",
                   "start_time": "2026-11-03T10:00:00+08:00"}],
    }), encoding="utf-8")
    return path


@pytest.fixture
def trajectory(trajectory_path):
    return normalize(load_trajectory(str(trajectory_path)), load_pricing())


@pytest.fixture
def mock_client(monkeypatch):
    original_client = judge.outbound.async_client
    requests = []
    options = []

    def install(handler):
        def dispatch(request):
            requests.append(request)
            return handler(request)

        def factory(**kwargs):
            options.append(kwargs.copy())
            client = original_client(transport=httpx.MockTransport(dispatch), **kwargs)
            assert client.trust_env is False
            assert client.follow_redirects is False
            return client

        monkeypatch.setattr(judge.outbound, "async_client", factory)
        return requests, options

    return install


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def finding(**updates):
    result = {"type": "hallucination", "span_id": "s1", "severity": "high",
              "detail": "  需要核实回答依据  ", "confidence": 0.9}
    result.update(updates)
    return result


def assert_safe(outcome, expected_note):
    assert outcome.note == "profile=review model=independent-judge：" + expected_note
    assert SECRET not in outcome.model_dump_json()
    assert UPSTREAM_SECRET not in outcome.model_dump_json()
    assert "upstream-private-body" not in outcome.note
    assert "judge.example" not in outcome.note


def test_registry_uses_independent_judge(config):
    assert config.profile_id == "review"
    assert config.model == "independent-judge"
    assert config.base_url == "https://judge.example/v1"
    assert config.api_key == SECRET
    assert config.api_key_env == "TEST_JUDGE_KEY"
    assert config.supports_json_object is False
    assert SECRET not in repr(config)
    assert config.model != "gateway-model"


def test_missing_judge_key_skips_without_gateway_or_legacy_fallback(
    registry_path, trajectory, monkeypatch, mock_client,
):
    monkeypatch.setenv("TEST_GATEWAY_KEY", UPSTREAM_SECRET)
    monkeypatch.setenv("SAFEORBIT_JUDGE_API_KEY", "legacy-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-deepseek-secret")
    config = JudgeConfig.from_registry(ModelRegistry.load(registry_path))
    requests, options = mock_client(lambda request: pytest.fail("缺少裁判密钥时不应请求网络"))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert config.api_key == ""
    assert outcome.status == "skipped"
    assert outcome.hits == []
    assert_safe(outcome, "未配置 TEST_JUDGE_KEY，跳过语义裁判")
    assert requests == options == []


@pytest.mark.parametrize("alias", ["chat", "unknown"])
def test_registry_rejects_non_judge_purpose(registry_path, alias):
    with pytest.raises(ValueError, match="未允许 judge 用途"):
        JudgeConfig.from_registry(ModelRegistry.load(registry_path), alias)


def test_catalog_without_judge_does_not_use_legacy_key(registry_path, monkeypatch):
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    data.pop("judge_profile")
    registry_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("SAFEORBIT_JUDGE_API_KEY", SECRET)
    assert JudgeConfig.from_registry(ModelRegistry.load(registry_path)) is None


@pytest.mark.parametrize("supports_json_object", [True, False])
def test_json_capability_and_empty_findings(
    config, trajectory, mock_client, supports_json_object,
):
    config.supports_json_object = supports_json_object
    requests, options = mock_client(lambda request: completion('{"findings":[]}'))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "ok"
    assert outcome.hits == []
    assert_safe(outcome, "裁判完成，产生 0 条建议复核结论")
    assert len(requests) == len(options) == 1
    request = requests[0]
    assert str(request.url) == "https://judge.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer " + SECRET
    payload = json.loads(request.content)
    assert payload["model"] == "independent-judge"
    assert payload["temperature"] == 0
    # 结论需附原文摘录与引用编号，并为推理模型预留足够输出额度
    assert payload["max_tokens"] == 3200
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "judge-test" in payload["messages"][1]["content"]
    assert SECRET not in request.content.decode()
    assert UPSTREAM_SECRET not in request.content.decode()
    if supports_json_object:
        assert payload["response_format"] == {"type": "json_object"}
    else:
        assert "response_format" not in payload
    assert options[0] == {"timeout": 30.0}


@pytest.mark.parametrize("content", [
    "not JSON upstream-private-body " + SECRET,
    "[]", "null", "{}", '{"findings":{}}', '{"findings":[null]}',
    json.dumps({"findings": [finding(type="unknown")]}),
    json.dumps({"findings": [finding(severity="fatal")]}),
    json.dumps({"findings": [finding(detail=" ")]}),
    json.dumps({"findings": [finding(detail=123)]}),
    json.dumps({"findings": [finding(confidence=True)]}),
    json.dumps({"findings": [finding(confidence="0.9")]}),
    json.dumps({"findings": [finding(confidence=-0.1)]}),
    json.dumps({"findings": [finding(confidence=1.1)]}),
], ids=["bad-json", "array", "null", "missing-findings", "wrong-findings", "invalid-item",
        "type", "severity", "empty-detail", "detail-type", "bool-confidence",
        "string-confidence", "negative-confidence", "large-confidence"])
def test_invalid_content_retries_exactly_two_attempts(config, trajectory, mock_client, content):
    requests, options = mock_client(lambda request: completion(content))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "error"
    assert outcome.hits == []
    assert len(requests) == len(options) == 2
    assert_safe(outcome, "裁判调用失败：响应结构或 JSON 无效")


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {}}]}])
def test_invalid_completion_envelope_retries(config, trajectory, mock_client, body):
    requests, _ = mock_client(lambda request: httpx.Response(200, json=body))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "error"
    assert len(requests) == 2
    assert_safe(outcome, "裁判调用失败：响应结构或 JSON 无效")


def test_retry_can_recover(config, trajectory, mock_client):
    responses = iter([completion("bad JSON"), completion('{"findings":[]}')])
    requests, _ = mock_client(lambda request: next(responses))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "ok"
    assert len(requests) == 2


def test_retry_count_is_configurable(config, trajectory, mock_client):
    config.max_retries = 3
    requests, _ = mock_client(lambda request: completion("bad JSON"))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "error"
    assert len(requests) == 4


@pytest.mark.parametrize("span_id", ["invented", "", None, 123, ["s1"]])
def test_invalid_span_is_discarded(config, trajectory, mock_client, span_id):
    requests, _ = mock_client(lambda request: completion(json.dumps({
        "findings": [finding(span_id=span_id)],
    })))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "ok"
    assert outcome.hits == []
    assert len(requests) == 1


def test_valid_hit_requires_review_and_preserves_real_span(config, trajectory, mock_client):
    requests, _ = mock_client(lambda request: completion(json.dumps({
        "findings": [finding(span_id="invented"), finding()],
    })))
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "ok"
    assert len(requests) == len(outcome.hits) == 1
    hit = outcome.hits[0]
    assert hit.rule_id == "judge.hallucination"
    assert hit.span_ref == "s1"
    assert hit.dimension == Dimension.QUALITY
    assert hit.severity == Severity.HIGH
    assert hit.message == "需要核实回答依据"
    assert hit.confidence == 0.9
    assert hit.source == "judge"
    assert hit.needs_review is True
    assert_safe(outcome, "裁判完成，产生 1 条建议复核结论；模型结论需人工复核")


@pytest.mark.parametrize("failure,expected", [
    ("timeout", "请求超时"), (401, "HTTP 401"), (429, "HTTP 429"),
    (302, "HTTP 302"), (307, "HTTP 307"), ("network", "网络错误"),
])
def test_transport_failures_are_safe_and_do_not_follow_redirects(
    config, trajectory, mock_client, failure, expected,
):
    def handler(request):
        private = "upstream-private-body " + SECRET + " https://judge.example/private"
        if failure == "timeout":
            raise httpx.ReadTimeout(private, request=request)
        if failure == "network":
            raise httpx.ConnectError(private, request=request)
        return httpx.Response(failure, text=private, headers={
            "Location": "https://redirect.example/steal?key=" + SECRET,
        })

    requests, options = mock_client(handler)
    outcome = asyncio.run(judge_trajectory(trajectory, config))
    assert outcome.status == "error"
    assert outcome.hits == []
    assert len(requests) == len(options) == 2
    assert all(str(request.url) == "https://judge.example/v1/chat/completions" for request in requests)
    assert all(option == {"timeout": 30.0} for option in options)
    assert_safe(outcome, "裁判调用失败：" + expected)


def test_cli_model_parameters(registry_path, trajectory_path):
    args = cli._build_parser().parse_args([
        "audit", str(trajectory_path), "--models-config", str(registry_path),
        "--judge-profile", "review", "--judge-model", "legacy-only",
    ])
    assert args.models_config == str(registry_path)
    assert args.judge_profile == "review"
    assert args.judge_model == "legacy-only"
    args = cli._build_parser().parse_args([
        "serve", "--models-config", str(registry_path), "--judge-profile", "review", "--judge",
    ])
    assert args.models_config == str(registry_path)
    assert args.judge_profile == "review"
    assert args.judge is True


def test_cli_catalog_rejects_judge_model(registry_path, trajectory_path, tmp_path, capsys):
    output = tmp_path / "reports"
    assert cli.main([
        "audit", str(trajectory_path), "--models-config", str(registry_path),
        "--judge-model", "legacy-only", "--out", str(output),
    ]) == 2
    assert "目录模式不能使用 --judge-model，请使用 --judge-profile" in capsys.readouterr().err
    assert not output.exists()
