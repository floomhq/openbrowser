from __future__ import annotations

from pathlib import Path

from ax_browser_broker.docs import docs


def test_docs_topics_and_quickstart() -> None:
    topics = docs("topics")
    assert "quickstart" in topics["topics"]
    assert "routing" in topics["topics"]
    assert "telemetry" in topics["topics"]
    assert "audit" in topics["topics"]

    quickstart = docs("quickstart")
    assert quickstart["topic"] == "quickstart"
    assert quickstart["examples"][0]["tool"] == "browser_lease"

    identities = docs("identities")
    assert any("Auto Chrome identities do not overwrite" in item for item in identities["facts"])
    assert any("Keychain" in item for item in identities["facts"])
    assert any("error telemetry" in item for item in identities["facts"])
    assert any("lease-state lock" in item for item in identities["facts"])
    assert "/root/ax-browser-broker/docs/mac-chrome-profiles.md" in identities["runbooks"]

    auth = docs("auth")
    assert any("temporary VNC password file is removed" in item for item in auth["steps"])

    routing = docs("routing")
    assert routing["topic"] == "routing"
    assert "browser-routing.md" in routing["runbooks"][0]
    assert any("OpenBrowser is an adapter" in route.get("note", "") for route in routing["routes"])
    assert any("chrome-depontefede" in route.get("start", "") for route in routing["routes"])
    assert any("discord-main" in route.get("start", "") for route in routing["routes"])
    assert any("9223" in rule and "broker lease manager" in rule for rule in routing["rules"])
    assert any("chrome-depontefede" in rule and "discord-main" in rule for rule in routing["rules"])


def test_docs_unknown_topic_lists_available_topics() -> None:
    result = docs("missing")
    assert "error" in result
    assert "feedback" in result["available_topics"]
    assert "telemetry" in result["available_topics"]
    assert "audit" in result["available_topics"]


def test_mac_chrome_runbook_documents_keychain_fallback_and_no_raw_token_path() -> None:
    text = Path("/root/ax-browser-broker/docs/mac-chrome-profiles.md").read_text(encoding="utf-8")

    assert "macOS Keychain material" in text
    assert "no raw-token fallback path by design" in text
    assert "human auth into the AX41 profile" in text
    assert "Chrome Sync" in text
