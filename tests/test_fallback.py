import json

from fallback import agent_builder as ab, lint_cli


def test_trace_line_redacts_env_and_patterns(monkeypatch):
    monkeypatch.setenv("FREYA_API_KEY", "abcdefgh12345678")
    line = ab.trace_line({"type": "ToolUseBlock", "input": {"command": "echo abcdefgh12345678 fak_Zz9 sk-ant-api03-xyz"}})
    assert "abcdefgh12345678" not in line and "fak_Zz9" not in line and "sk-ant-api03-xyz" not in line
    json.loads(line)


def test_options_strict_and_denied():
    o = ab.make_options()
    assert o.strict_mcp_config and o.mcp_servers == {} and o.skills == ["build-phone-agent-v2"]
    assert o.setting_sources == ["project"]
    assert "Read(./env.sh)" in o.disallowed_tools and "Read(**/.env.*)" in o.disallowed_tools
    assert json.loads(o.settings)["permissions"]["deny"] == o.disallowed_tools
    assert o.permission_mode == "dontAsk"


def test_lint_cli_drops_spec_only_check():
    wf = json.load(open("examples/base-agent.json"))
    assert not any("spec outcome table" in e for e in lint_cli.problems(wf))


def test_swap_base_uses_openapi_servers():
    from fallback.selftest_cli import swap_base
    api = {"servers": [{"url": "https://example.test/api/v2"}]}
    out = swap_base('{"u": "https://example.test/api/v2/get_account"}', api, "https://t.trycloudflare.com")
    assert out == '{"u": "https://t.trycloudflare.com/v1/get_account"}'
