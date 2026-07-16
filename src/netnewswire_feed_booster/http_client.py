from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError
import urllib.request
from typing import Any, Mapping, Optional


USER_AGENT = "netnewswire-feed-booster/0.1"
DEFAULT_MAX_FETCH_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class FetchTextResponse:
    text: Optional[str]
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


def fetch_text(url: str, max_bytes: int = DEFAULT_MAX_FETCH_BYTES) -> str:
    response = fetch_text_response(url, max_bytes=max_bytes)
    if response.text is None:
        raise ValueError(f"Unexpected 304 Not Modified response without cached content: {url}")
    return response.text


def fetch_text_response(
    url: str,
    request_headers: Optional[Mapping[str, str]] = None,
    max_bytes: int = DEFAULT_MAX_FETCH_BYTES,
) -> FetchTextResponse:
    headers = {"User-Agent": USER_AGENT}
    if request_headers:
        headers.update(request_headers)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(max_bytes + 1)
            response_headers = getattr(response, "headers", {})
    except HTTPError as error:
        if error.code != 304:
            raise
        response_headers = error.headers or {}
        return FetchTextResponse(
            text=None,
            etag=response_headers.get("ETag", ""),
            last_modified=response_headers.get("Last-Modified", ""),
            not_modified=True,
        )
    if len(body) > max_bytes:
        raise ValueError(f"Response exceeded {max_bytes} byte limit: {url}")
    return FetchTextResponse(
        text=body.decode("utf-8", errors="replace"),
        etag=response_headers.get("ETag", ""),
        last_modified=response_headers.get("Last-Modified", ""),
    )


def fetch_json(url: str) -> dict[str, Any]:
    return json.loads(fetch_text(url))
