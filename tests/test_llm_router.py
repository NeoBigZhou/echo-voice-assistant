import yaml

from app import llm_router


def _route_entry():
    return {
        "displayName": "ECHO AUTO（本机模型组）",
        "apiKeyEnv": "ECHO_ROUTER_TOKEN",
        "api": "openai-completions",
        "baseURL": "http://127.0.0.1:8899",
        "retryPolicy": {"mode": "normal", "maxRetries": 2},
        "compat": {"thinkingFormat": "chat-template", "supportsDeveloperRole": False},
        "models": [{
            "id": "echo-auto",
            "name": "ECHO AUTO",
            "contextWindow": 1000000,
            "maxTokens": 256000,
            "reasoningEfforts": {"off": None, "high": "high", "max": "max"},
            "compat": {"thinkingFormat": "chat-template", "supportsDeveloperRole": False},
        }],
    }


def test_text_sync_creates_missing_pi_ai_section(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text("dsh-desktop:\n  mode: compatibility\n", encoding="utf-8")
    monkeypatch.setattr(llm_router, "SETTINGS", settings)
    monkeypatch.setattr(llm_router, "_route_entry", _route_entry)

    assert llm_router._sync_settings_text()
    doc = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert doc["dsh-desktop"]["mode"] == "compatibility"
    assert doc["llm-pi-ai"]["providers"]["echo-auto"]["models"][0]["id"] == "echo-auto"


def test_text_sync_creates_missing_providers_and_keeps_other_options(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "llm-pi-ai:\n  someOption: true\nui-onboarding:\n  welcomeNoticeVersion: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_router, "SETTINGS", settings)
    monkeypatch.setattr(llm_router, "_route_entry", _route_entry)

    assert llm_router._sync_settings_text()
    doc = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert doc["llm-pi-ai"]["someOption"] is True
    assert doc["llm-pi-ai"]["providers"]["echo-auto"]["models"]
    assert doc["ui-onboarding"]["welcomeNoticeVersion"] == 1
