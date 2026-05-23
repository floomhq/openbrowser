from __future__ import annotations

import pytest

from ax_browser_broker.iproyal import (
    IPRoyalClient,
    IPRoyalConfig,
    IPRoyalError,
    extract_proxy_records,
    isp_dedicated_summary,
    redact,
    redact_payload,
)


def test_redact() -> None:
    assert redact("abcd1234efgh") == "abcd...efgh"
    assert redact("short") == "***"


def test_isp_dedicated_summary_extracts_plan() -> None:
    data = {
        "data": [
            {
                "id": 9,
                "name": "ISP Dedicated",
                "plans": [{"id": 22, "name": "30 Days", "price": 4, "min_quantity": 1, "max_quantity": 100}],
                "locations": [{"id": 1, "name": "United States", "out_of_stock": False, "available_proxies_count": None}],
            }
        ]
    }
    summary = isp_dedicated_summary(data)
    assert summary["product"]["id"] == 9
    assert summary["plan"]["id"] == 22
    assert summary["plan"]["price"] == 4
    assert summary["locations"][0]["name"] == "United States"


def test_create_order_requires_confirm_spend() -> None:
    client = IPRoyalClient(IPRoyalConfig(api_key="test"))
    with pytest.raises(IPRoyalError, match="confirm_spend"):
        client.create_order(product_location_id=1)


def test_extract_proxy_records_from_nested_payload() -> None:
    payload = {
        "data": {
            "order": {
                "proxies": [
                    {
                        "ip": "203.0.113.10",
                        "port": 1234,
                        "username": "user",
                        "password": "pass",
                    }
                ]
            }
        }
    }

    assert extract_proxy_records(payload) == [
        {
            "scheme": "http",
            "host": "203.0.113.10",
            "port": 1234,
            "username": "user",
            "password": "pass",
        }
    ]


def test_redact_payload_masks_proxy_credentials() -> None:
    payload = {"proxy": {"username": "user-secret", "password": "pass-secret", "host": "203.0.113.10"}}

    assert redact_payload(payload) == {
        "proxy": {"username": "user...cret", "password": "pass...cret", "host": "203.0.113.10"}
    }
