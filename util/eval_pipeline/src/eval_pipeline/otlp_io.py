"""Fetch trace from viewer API and convert to JSONL format for scoring."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_STATUS_MAP = {0: "UNSET", 1: "OK", 2: "ERROR"}


def _otlp_attrs_to_dict(attrs: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert OTLP attribute array [{key, value}] to a flat dict."""
    result: dict[str, Any] = {}
    for attr in attrs or []:
        key = attr.get("key", "")
        value_obj = attr.get("value", {})
        if "stringValue" in value_obj:
            result[key] = value_obj["stringValue"]
        elif "intValue" in value_obj:
            result[key] = int(value_obj["intValue"])
        elif "doubleValue" in value_obj:
            result[key] = float(value_obj["doubleValue"])
        elif "boolValue" in value_obj:
            result[key] = value_obj["boolValue"]
        elif "arrayValue" in value_obj:
            result[key] = value_obj["arrayValue"].get("values", [])
        elif "kvlistValue" in value_obj:
            result[key] = {
                kv["key"]: kv.get("value") for kv in value_obj["kvlistValue"].get("values", [])
            }
    return result


def _otlp_span_to_jsonl(span: dict[str, Any], resource_attrs: dict[str, Any]) -> dict[str, Any]:
    """Convert one OTLP span dict to JSONL span format (snake_case, flat attributes)."""
    start_ns = int(span.get("startTimeUnixNano", "0"))
    end_ns = int(span.get("endTimeUnixNano", span.get("startTimeUnixNano", "0")))
    status = span.get("status", {})
    code = status.get("code", 0)
    status_code = _STATUS_MAP.get(code, "UNSET")

    events = []
    for ev in span.get("events", []):
        events.append(
            {
                "name": ev.get("name", ""),
                "timestamp": int(ev.get("timeUnixNano", "0")),
                "attributes": _otlp_attrs_to_dict(ev.get("attributes", [])),
            }
        )

    return {
        "span_id": span.get("spanId", ""),
        "trace_id": span.get("traceId", ""),
        "parent_span_id": span.get("parentSpanId"),
        "name": span.get("name", ""),
        "start_time": start_ns,
        "end_time": end_ns,
        "duration_ns": end_ns - start_ns,
        "attributes": _otlp_attrs_to_dict(span.get("attributes", [])),
        "events": events,
        "status": {
            "status_code": status_code,
            "description": status.get("message"),
        },
        "resource": {"attributes": resource_attrs},
    }


def fetch_trace_as_jsonl(
    session_id: str,
    viewer_base_url: str | None = None,
    *,
    temp_dir: Path | None = None,
) -> Path:
    """Fetch a session's trace from the viewer API and write it as JSONL to a temp file.

    The file format is one JSON object per line (same as the JSONL exporter),
    so existing scorers can read it via extract_code_from_trace etc.

    Args:
        session_id: Session id (same as used when sending spans).
        viewer_base_url: Base URL of the viewer (e.g. http://localhost:5001).
                        Defaults to OTLP_ENDPOINT with /v1/traces stripped.
        temp_dir: Directory for the temp file. Defaults to system temp.

    Returns:
        Path to the temp file. Caller may delete it after scoring.

    Raises:
        FileNotFoundError: If the session is not found (404).
        Exception: On other HTTP or network errors.
    """
    if viewer_base_url is None:
        endpoint = os.getenv("OTLP_ENDPOINT", "http://localhost:5001/v1/traces")
        viewer_base_url = endpoint.rstrip("/").removesuffix("/v1/traces") or "http://localhost:5001"

    url = f"{viewer_base_url.rstrip('/')}/api/trace?session_id={session_id}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                raise FileNotFoundError(f"GET {url} returned {resp.status}")
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(f"Session not found: {session_id}") from e
        raise

    events = data.get("events", [])
    lines = []
    for span in events:
        resource = span.get("_resource", {})
        res_attrs = _otlp_attrs_to_dict(resource.get("attributes", []))
        jsonl_span = _otlp_span_to_jsonl(span, res_attrs)
        lines.append(json.dumps(jsonl_span, default=str))

    fd, path = tempfile.mkstemp(
        suffix=".jsonl",
        prefix=f"trace_{session_id}_",
        dir=temp_dir,
        text=True,
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines))
            if lines:
                f.write("\n")
    except Exception:
        os.unlink(path)
        raise

    return Path(path)
