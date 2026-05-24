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


def test_docs_unknown_topic_lists_available_topics() -> None:
    result = docs("missing")
    assert "error" in result
    assert "feedback" in result["available_topics"]
    assert "telemetry" in result["available_topics"]
    assert "audit" in result["available_topics"]
