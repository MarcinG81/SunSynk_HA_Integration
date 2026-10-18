"""Show what Deye Cloud reports for your inverters, to help add Deye Cloud support.

Read-only: it logs in, lists your inverters and prints their latest measure
points and settings (system, battery, time of use). It changes nothing.
Serial numbers, names, IDs and location are masked, so the output is safe to
paste into a GitHub issue or discussion.

Needs only Python 3.9 or newer, nothing to install. You need a Deye Cloud
developer application (App ID and App Secret) from
https://developer.deyecloud.com/app and your Deye Cloud login.

    python deyecloud_probe.py --region eu --app-id YOUR_APP_ID --login you@example.com

It asks for the App Secret and password without showing them, and saves the
result to deyecloud_probe.json.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import sys
import urllib.request
from typing import Any

# Same data centres and endpoints as custom_components/sunsynk/deyecloud.
REGIONS = {
    "eu": "https://eu1-developer.deyecloud.com/v1.0",
    "us": "https://us1-developer.deyecloud.com/v1.0",
    "india": "https://india-developer.deyecloud.com/v1.0",
}
_SUCCESS_CODES = {0, 1000000, 1106000}

# Identifying fields: serials ("deviceSn", "loggerSn"), IDs ("stationId"),
# names, addresses, contact details and location. Matched on whole words so
# measure points such as "CumulativeGridFeedIn" are left alone.
_SENSITIVE_KEYS = re.compile(
    r"(Sn|Id|Name)$|^(sn|id|name|serial|address|email|phone|mobile|owner|"
    r"user(name)?|lat(itude)?|lon(gitude)?|lng)$",
    re.IGNORECASE,
)


def _mask(value: Any) -> Any:
    text = str(value)
    return text[:2] + "*" * max(0, len(text) - 4) + text[-2:] if len(text) > 4 else "****"


def redact(data: Any, key: str = "") -> Any:
    """Mask serials, names, IDs and personal details, keep everything else."""
    if isinstance(data, dict):
        return {k: redact(v, k) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v, key) for v in data]
    if key and _SENSITIVE_KEYS.search(key) and data not in (None, ""):
        return _mask(data)
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # never send credentials on to another address


def post(url: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"bearer {token}"} if token else {}),
        },
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _ok(result: dict[str, Any]) -> bool:
    try:
        return int(result.get("code")) in _SUCCESS_CODES
    except (TypeError, ValueError):
        return result.get("success") is True


def _app_id_hint(region: str, app_id: str) -> str:
    """Which data centre knows this App ID, if not the one asked for."""
    known = []
    for other, base in REGIONS.items():
        if other == region:
            continue
        try:
            result = post(f"{base}/account/token?appId={app_id}", {"appSecret": ""})
        except Exception:  # noqa: BLE001 - only a hint
            continue
        if "appid" not in str(result.get("msg", "")).lower():
            known.append(other)
    shown = f"{app_id[:3]}...{app_id[-3:]} ({len(app_id)} characters)"
    if known:
        return f"\nApp ID {shown} is known in region {', '.join(known)}: try --region {known[0]}"
    return (
        f"\nApp ID {shown} is not known in any data centre. Check it is the App ID"
        " (not the App Secret or the app name), copied whole."
    )


def probe(
    region: str,
    app_id: str,
    secret: str,
    login: str,
    password: str,
    company_id: str | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    app_id, secret, login = app_id.strip(), secret.strip(), login.strip()
    base = REGIONS[region]
    body: dict[str, Any] = {
        "appSecret": secret,
        "password": hashlib.sha256(password.encode()).hexdigest(),
        ("email" if "@" in login else "username"): login,
    }
    if company_id:
        body["companyId"] = company_id
    login_result = post(f"{base}/account/token?appId={app_id}", body)
    token = str(login_result.get("accessToken") or "").split(" ", 1)[-1]
    if not _ok(login_result) or not token:
        message = str(login_result.get("msg") or login_result)
        if "appid" in message.lower():
            message += _app_id_hint(region, app_id)
        raise SystemExit(f"Login failed: {message}")

    def call(path: str, payload: dict[str, Any]) -> Any:
        try:
            result = post(f"{base}{path}", payload, token)
        except Exception as err:  # noqa: BLE001 - report, don't stop
            return {"error": str(err)}
        return result.get("data", result) if _ok(result) else {"error": result}

    stations = call(
        "/station/listWithDevice", {"page": 1, "size": 50, "deviceType": "INVERTER"}
    )
    serials = [
        device["deviceSn"]
        for station in (stations.get("stationList") or [])
        for device in (station.get("deviceListItems") or [])
        if device.get("deviceSn")
    ][:limit]
    report: dict[str, Any] = {"stations": stations, "devices": {}}
    latest = call("/device/latest", {"deviceList": serials}) if serials else {}
    for number, serial in enumerate(serials, start=1):
        device_latest = next(
            (
                d
                for d in (latest.get("deviceDataList") or [])
                if d.get("deviceSn") == serial
            ),
            {},
        )
        # Numbered, not keyed by serial: keys aren't masked.
        report["devices"][f"inverter_{number}"] = {
            "latest": device_latest.get("dataList", []),
            **{
                kind: call(f"/config/{kind}", {"deviceSn": serial})
                for kind in ("system", "battery", "tou")
            },
        }
    return redact(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--region", choices=sorted(REGIONS), required=True)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--login", required=True, help="Deye Cloud email or username")
    parser.add_argument("--company-id", help="only for business accounts")
    parser.add_argument("--limit", type=int, default=3, help="inverters to show")
    parser.add_argument("--output", default="deyecloud_probe.json")
    args = parser.parse_args()
    secret = getpass.getpass("App Secret: ")
    password = getpass.getpass("Deye Cloud password: ")
    report = probe(
        args.region,
        args.app_id,
        secret,
        args.login,
        password,
        args.company_id,
        args.limit,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    print(text)
    print(f"\nSaved to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
