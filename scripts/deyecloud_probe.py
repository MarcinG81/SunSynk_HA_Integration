"""Show what Deye Cloud reports for your inverters, to help add Deye Cloud support.

Read-only: it logs in, lists your inverters and prints their latest measure
points and settings (system, battery, time of use). It changes nothing.
Serial numbers, names and IDs are masked, so the output is safe to paste
into a GitHub issue or discussion.

You need a Deye Cloud developer application (App ID and App Secret) from
https://developer.deyecloud.com/app and your Deye Cloud login.

Usage (from the repository root, Python 3.11+ with aiohttp installed):

    python -m scripts.deyecloud_probe --region eu --app-id ID --login you@example.com

It asks for the App Secret and password without echoing them.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
import sys
from typing import Any

import aiohttp

from custom_components.sunsynk.deyecloud.client import (
    REGIONS,
    DeyeCloudClient,
    hash_password,
)

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


async def probe(args: argparse.Namespace, secret: str, password: str) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        client = DeyeCloudClient(
            session,
            args.region,
            args.app_id,
            secret,
            args.login,
            hash_password(password),
            company_id=args.company_id,
        )
        inverters = await client.async_list_inverters()
        report: dict[str, Any] = {"inverters": inverters, "devices": {}}
        serials = [inv["deviceSn"] for inv in inverters][: args.limit]
        latest = await client.async_get_latest(serials) if serials else {}
        for serial in serials:
            device: dict[str, Any] = {"latest": latest.get(serial, {})}
            for kind in ("system", "battery", "tou"):
                try:
                    device[kind] = await client.async_get_config(serial, kind)
                except Exception as err:  # noqa: BLE001 - report, don't stop
                    device[kind] = {"error": str(err)}
            report["devices"][serial] = device
        return redact(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", choices=sorted(REGIONS), required=True)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--login", required=True, help="Deye Cloud email or username")
    parser.add_argument("--company-id", help="only for business accounts")
    parser.add_argument("--limit", type=int, default=3, help="inverters to show")
    args = parser.parse_args()
    secret = getpass.getpass("App Secret: ")
    password = getpass.getpass("Deye Cloud password: ")
    report = asyncio.run(probe(args, secret, password))
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
