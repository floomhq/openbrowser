from __future__ import annotations

from ax_browser_broker.docs import docs


def test_docs_topics_and_quickstart() -> None:
    topics = docs("topics")
    assert "quickstart" in topics["topics"]
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


def test_docs_unknown_topic_lists_available_topics() -> None:
    result = docs("missing")
    assert "error" in result
    assert "feedback" in result["available_topics"]
    assert "telemetry" in result["available_topics"]
    assert "audit" in result["available_topics"]
