"""Shared by the FAKE products of the v3 adapter suites: the call log (NVT3_FAKE_LOG) and a JSON POST through
urllib.request.urlopen, looked up at call time so the adapter's counter and pacer see every request."""
from __future__ import annotations

import json
import os
import urllib.request

_UNSET = object()


def log(event: str, **kw) -> None:
    path = os.environ.get("NVT3_FAKE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, **kw}, default=str, sort_keys=True) + "\n")


def post(url: str, obj, bearer: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")
