from __future__ import annotations

import json
import urllib.request
from typing import Any


USER_AGENT = "netnewswire-feed-booster/0.1"
DEFAULT_MAX_FETCH_BYTES = 10 * 1024 * 1024


def fetch_text(url: str, max_bytes: int = DEFAULT_MAX_FETCH_BYTES) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"Response exceeded {max_bytes} byte limit: {url}")
    return body.decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict[str, Any]:
    return json.loads(fetch_text(url))
