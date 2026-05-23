from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_BASE = "https://apid.iproyal.com/v1/reseller"
DEFAULT_SECRET_FILE = Path("/root/ax-browser-broker/secrets/iproyal.json")
ISP_DEDICATED_PRODUCT_ID = 9
ISP_DEDICATED_30_DAY_PLAN_ID = 22


class IPRoyalError(RuntimeError):
    pass


@dataclass(frozen=True)
class IPRoyalConfig:
    api_key: str


def load_config(path: Path = DEFAULT_SECRET_FILE) -> IPRoyalConfig:
    if not path.exists():
        raise IPRoyalError(f"IPRoyal secret file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    key = str(data.get("api_key", "")).strip()
    if not key:
        raise IPRoyalError("IPRoyal api_key is missing")
    return IPRoyalConfig(api_key=key)


def redact(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= 8:
        return "***"
    return text[:4] + "..." + text[-4:]


def redact_payload(value: Any) -> Any:
    sensitive_keys = {"password", "pass", "username", "user", "login", "api_key", "token"}
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            if str(key).lower() in sensitive_keys:
                redacted[key] = redact(str(child)) if child is not None else None
            else:
                redacted[key] = redact_payload(child)
        return redacted
    if isinstance(value, list):
        return [redact_payload(child) for child in value]
    return value


class IPRoyalClient:
    def __init__(self, config: IPRoyalConfig | None = None) -> None:
        self.config = config or load_config()

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            API_BASE + path,
            data=data,
            method=method,
            headers={
                "X-Access-Token": self.config.api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            raise IPRoyalError(f"IPRoyal HTTP {error.code}: {raw[:1000]}") from error
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def balance(self) -> Any:
        return self.request("GET", "/balance")

    def products(self) -> dict[str, Any]:
        return self.request("GET", "/products")

    def orders(self, product_id: int = ISP_DEDICATED_PRODUCT_ID) -> dict[str, Any]:
        query = urllib.parse.urlencode({"product_id": product_id, "page": 1, "per_page": 50})
        return self.request("GET", f"/orders?{query}")

    def calculate_pricing(
        self,
        product_location_id: int,
        quantity: int = 1,
        product_id: int = ISP_DEDICATED_PRODUCT_ID,
        product_plan_id: int = ISP_DEDICATED_30_DAY_PLAN_ID,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "product_id": product_id,
                "product_plan_id": product_plan_id,
                "product_location_id": product_location_id,
                "quantity": quantity,
            }
        )
        return self.request("GET", f"/orders/calculate-pricing?{query}")

    def create_order(
        self,
        product_location_id: int,
        quantity: int = 1,
        product_id: int = ISP_DEDICATED_PRODUCT_ID,
        product_plan_id: int = ISP_DEDICATED_30_DAY_PLAN_ID,
        auto_extend: bool = False,
        confirm_spend: bool = False,
    ) -> dict[str, Any]:
        if not confirm_spend:
            raise IPRoyalError("Refusing to create paid order without confirm_spend=True")
        return self.request(
            "POST",
            "/orders",
            {
                "product_id": product_id,
                "product_plan_id": product_plan_id,
                "product_location_id": product_location_id,
                "quantity": quantity,
                "auto_extend": auto_extend,
            },
        )


def isp_dedicated_summary(products: dict[str, Any]) -> dict[str, Any]:
    product = next((item for item in products.get("data", []) if item.get("id") == ISP_DEDICATED_PRODUCT_ID), None)
    if not product:
        raise IPRoyalError("ISP Dedicated product not found")
    plan = next((item for item in product.get("plans", []) if item.get("id") == ISP_DEDICATED_30_DAY_PLAN_ID), None)
    if not plan:
        raise IPRoyalError("ISP Dedicated 30 Days plan not found")
    locations = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "out_of_stock": item.get("out_of_stock"),
            "available_proxies_count": item.get("available_proxies_count"),
        }
        for item in product.get("locations", [])
    ]
    return {
        "product": {"id": product.get("id"), "name": product.get("name")},
        "plan": {
            "id": plan.get("id"),
            "name": plan.get("name"),
            "price": plan.get("price"),
            "min_quantity": plan.get("min_quantity"),
            "max_quantity": plan.get("max_quantity"),
        },
        "locations": locations,
    }


def extract_proxy_records(payload: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            host = value.get("host") or value.get("hostname") or value.get("ip") or value.get("address")
            port = value.get("port") or value.get("http_port") or value.get("socks5_port")
            username = value.get("username") or value.get("user") or value.get("login")
            password = value.get("password") or value.get("pass")
            if host and port and username and password:
                records.append(
                    {
                        "scheme": str(value.get("scheme") or value.get("protocol") or "http").lower(),
                        "host": str(host),
                        "port": int(port),
                        "username": str(username),
                        "password": str(password),
                    }
                )
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IPRoyal helper for AX41 Browser Broker")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("balance")
    sub.add_parser("isp-summary")
    orders = sub.add_parser("orders")
    orders.add_argument("--product-id", type=int, default=ISP_DEDICATED_PRODUCT_ID)
    pricing = sub.add_parser("pricing")
    pricing.add_argument("--location-id", type=int, required=True)
    pricing.add_argument("--quantity", type=int, default=1)
    buy = sub.add_parser("buy-isp-30d")
    buy.add_argument("--location-id", type=int, required=True)
    buy.add_argument("--quantity", type=int, default=1)
    buy.add_argument("--confirm-spend", action="store_true")
    buy.add_argument("--store-ref")
    buy.add_argument("--country")
    args = parser.parse_args(argv)

    client = IPRoyalClient()
    if args.cmd == "balance":
        print(json.dumps({"balance": client.balance()}, indent=2))
    elif args.cmd == "isp-summary":
        print(json.dumps(isp_dedicated_summary(client.products()), indent=2))
    elif args.cmd == "orders":
        print(json.dumps(client.orders(args.product_id), indent=2))
    elif args.cmd == "pricing":
        print(json.dumps(client.calculate_pricing(args.location_id, args.quantity), indent=2))
    elif args.cmd == "buy-isp-30d":
        order = client.create_order(args.location_id, args.quantity, confirm_spend=args.confirm_spend)
        if args.store_ref:
            from .identities import save_proxy

            records = extract_proxy_records(order)
            if not records:
                raise IPRoyalError("Order created, but no proxy credentials were found in the response")
            proxy = records[0]
            if args.country:
                proxy["country"] = args.country
            save_proxy(args.store_ref, proxy)
            print(json.dumps({"stored_ref": args.store_ref, "order": redact_payload(order)}, indent=2))
        else:
            print(json.dumps(redact_payload(order), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
