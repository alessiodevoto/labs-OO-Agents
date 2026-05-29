# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Import NeMo OO Agents OTLP traces from a Harbor job directory into the viewer.

Walks a Harbor job directory (or any directory containing one), finds all
traces under ``artifacts/traces/*.jsonl``, enriches them with Harbor metadata
(trial name, task name, reward score, experiment grouping), and posts them to
the viewer.

Usage:
    nemo_oo_agents import-harbor ./jobs/my-job/
    nemo_oo_agents import-harbor ./workspaces/ --endpoint http://host:5001
    nemo_oo_agents import-harbor ./jobs/ --experiment my-eval --batch-id run-42
"""

import json
import urllib.parse
from pathlib import Path

import click

from ._otlp_helpers import (
    check_endpoint_reachable,
    inject_resource_attrs,
    post_traces_batch,
    session_exists,
    validate_endpoint,
)

NAME = "import-harbor"


def _find_harbor_traces(root: Path) -> list[Path]:
    """Find all OTLP trace files nested under Harbor artifact directories.

    Harbor copies the container's ``/logs/artifacts/`` to ``trial_dir/artifacts/``
    on the host. The agent decides the layout within that directory — a common
    convention is ``artifacts/traces/*.jsonl``, but we search the full subtree
    to be robust to other layouts.
    """
    return sorted(root.rglob("artifacts/**/*.jsonl"))


def _read_json(path: Path) -> dict:
    """Read a JSON file, returning an empty dict on any failure."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _coerce_float(value: object) -> float | None:
    """Coerce a value to float, returning None if it cannot be coerced."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def _read_score(trial_dir: Path, trial_result: dict) -> float | None:
    """Read the trial reward score, trying current Harbor result shapes in order.

    Different Harbor/BinPool versions write the scalar reward in different places.
    Fallback order (first coercible float wins):

    1. ``verifier/reward.json["score"]``
    2. ``verifier/reward.json["reward"]``
    3. ``result.json["verifier_result"]["rewards"]["score"]``
    4. ``result.json["verifier_result"]["rewards"]["reward"]``
    5. ``verifier/reward.txt`` (plain float string)

    Explicit ``None`` checks (not truthiness) ensure a valid ``0.0`` is returned.
    """
    reward_json = _read_json(trial_dir / "verifier" / "reward.json")
    for key in ("score", "reward"):
        score = _coerce_float(reward_json.get(key))
        if score is not None:
            return score

    verifier_result = trial_result.get("verifier_result")
    rewards = verifier_result.get("rewards") if isinstance(verifier_result, dict) else None
    if isinstance(rewards, dict):
        for key in ("score", "reward"):
            score = _coerce_float(rewards.get(key))
            if score is not None:
                return score

    reward_txt = trial_dir / "verifier" / "reward.txt"
    if reward_txt.exists():
        return _coerce_float(reward_txt.read_text().strip())

    return None


def _trial_meta(jsonl_path: Path) -> dict:
    """Extract Harbor metadata for a trace file from its surrounding directory structure.

    Expected layout::

        <job_dir>/
            result.json              ← job-level (stats.evals for experiment name)
            <trial_name>/
                result.json          ← trial_name, task_name, agent_info
                verifier/
                    reward.json      ← {"reward"|"score": <float>}  (or reward.txt)
                artifacts/           ← copy of /logs/artifacts/ from container
                    [traces/]        ← agent-defined layout; traces can be here
                        <file>.jsonl ← this file
    """
    # Walk up from the JSONL file to find the 'artifacts' directory;
    # trial_dir is its parent (works regardless of depth under artifacts/).
    trial_dir = jsonl_path.parent
    for parent in jsonl_path.parents:
        if parent.name == "artifacts":
            trial_dir = parent.parent
            break
    job_dir = trial_dir.parent

    trial_result = _read_json(trial_dir / "result.json")
    job_result = _read_json(job_dir / "result.json")

    trial_name = trial_result.get("trial_name") or trial_dir.name
    task_name = trial_result.get("task_name", "")
    agent_name = (trial_result.get("agent_info") or {}).get("name", "")

    # Reward scalar lives in different places across Harbor versions; see _read_score.
    score = _read_score(trial_dir, trial_result)

    # Use the eval key from the job-level result as the experiment name.
    # There is normally exactly one key (e.g. "WheelAgent__eval_service_train_…").
    experiment = ""
    evals = (job_result.get("stats") or {}).get("evals") or {}
    if evals:
        experiment = next(iter(evals))

    return {
        "trial_name": trial_name,
        "task_name": task_name,
        "agent_name": agent_name,
        "score": score,
        "experiment": experiment or "harbor",
        "job_name": job_dir.name,
    }


def _import_trace_file(
    endpoint: str,
    jsonl_path: Path,
    resource_attrs: dict[str, str | bool | int],
    batch_lines: int,
    batch_bytes: int,
) -> tuple[bool, list[str]]:
    """Import one OTLP JSONL file, posting its lines in batches.

    Accumulates OTLP bodies and flushes them in batches: many ``resourceSpans``
    envelopes are merged into one POST, avoiding one HTTP request per line. A flush
    is triggered when the batch reaches ``batch_lines`` envelopes or ``batch_bytes``
    of raw input (an approximation of the eventual POST size). Returns
    ``(file_imported, errors)`` where ``file_imported`` is True if any flush
    succeeded (preserving the previous any-success semantics).
    """
    file_imported = False
    errors: list[str] = []
    batch: list[dict] = []
    batch_input_bytes = 0
    flush_count = 0

    def flush() -> None:
        nonlocal file_imported, batch, batch_input_bytes, flush_count
        if not batch:
            return
        flush_count += 1
        if post_traces_batch(endpoint, batch):
            file_imported = True
        else:
            errors.append(f"{jsonl_path.name}: batch #{flush_count} failed to post")
        batch = []
        batch_input_bytes = 0

    with open(jsonl_path) as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                body = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if "resourceSpans" not in body:
                continue

            inject_resource_attrs(body, resource_attrs)
            batch.append(body)
            # Approximation: raw line length before injection; the re-serialized
            # POST body (with injected resource attrs) is slightly larger.
            batch_input_bytes += len(raw_line)

            if len(batch) >= batch_lines or batch_input_bytes >= batch_bytes:
                flush()

        flush()

    return file_imported, errors


@click.command()
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--endpoint",
    default="http://localhost:5001",
    show_default=True,
    help="Viewer API endpoint.",
)
@click.option(
    "--experiment",
    default=None,
    help="Override experiment name (default: auto-detected from job result.json).",
)
@click.option(
    "--batch-id",
    default=None,
    help="Batch ID for this import (default: job directory name).",
)
@click.option(
    "--batch-lines",
    default=1000,
    show_default=True,
    help="Max OTLP lines combined into a single POST (per trace file).",
)
@click.option(
    "--batch-bytes",
    default=4_000_000,
    show_default=True,
    help="Max raw input bytes accumulated before flushing a POST (per trace file).",
)
def command(
    path: str,
    endpoint: str,
    experiment: str | None,
    batch_id: str | None,
    batch_lines: int,
    batch_bytes: int,
):
    """Import NeMo OO Agents OTLP traces from a Harbor job directory.

    \b
    PATH can be:
      - A Harbor job directory (contains result.json + trial subdirs)
      - Any parent directory — traces are discovered recursively

    \b
    Examples:
        nemo_oo_agents import-harbor ./jobs/my-job/
        nemo_oo_agents import-harbor ./workspaces/ --endpoint http://host:5001
        nemo_oo_agents import-harbor ./jobs/ --experiment my-eval
        nemo_oo_agents import-harbor ./jobs/ --batch-lines 2000 --batch-bytes 8000000

    OTLP lines are posted in batches (combining many resourceSpans into one
    request) to keep large imports fast; tune with --batch-lines/--batch-bytes.
    """
    root = Path(path)
    files = _find_harbor_traces(root)

    if not files:
        click.echo(f"No Harbor trace files found under {path}")
        click.echo("Expected: <job>/<trial>/artifacts/traces/*.jsonl")
        raise SystemExit(1)

    validate_endpoint(endpoint)

    if not check_endpoint_reachable(endpoint):
        click.echo(f"Cannot reach viewer at {endpoint}. Is it running?")
        raise SystemExit(1)

    click.echo(f"Found {len(files)} trace file(s)...")

    imported = 0
    skipped = 0
    already_exist = 0
    errors = []

    for jsonl_path in files:
        meta = _trial_meta(jsonl_path)
        session_id = meta["trial_name"]
        exp = experiment or meta["experiment"]
        bid = batch_id or meta["job_name"]

        if session_exists(endpoint, session_id):
            click.echo(f"  ! {session_id}: already exists, skipping")
            already_exist += 1
            continue

        # Attributes to inject into the OTLP resource.
        # session.id uses the human-readable trial name rather than the
        # opaque timestamp filename stem.
        resource_attrs: dict[str, str | bool] = {
            "session.id": session_id,
            "experiment": exp,
            "batch_id": bid,
        }
        if meta["task_name"]:
            resource_attrs["eval.task_name"] = meta["task_name"]
        if meta["agent_name"]:
            resource_attrs["eval.agent_name"] = meta["agent_name"]
        if meta["score"] is not None:
            resource_attrs["eval.score"] = str(meta["score"])
            resource_attrs["eval.passed"] = meta["score"] >= 1.0

        file_imported, file_errors = _import_trace_file(
            endpoint, jsonl_path, resource_attrs, batch_lines, batch_bytes
        )
        errors.extend(file_errors)

        if file_imported:
            imported += 1
            score_str = f"{meta['score']:.3f}" if meta["score"] is not None else "n/a"
            click.echo(f"  + {session_id}  score={score_str}  task={meta['task_name']}")
        else:
            skipped += 1

    click.echo(f"\n{imported} imported, {skipped} skipped, {already_exist} already existed")
    if errors:
        for err in errors[:10]:
            click.echo(f"  ! {err}")
        if len(errors) > 10:
            click.echo(f"  ... and {len(errors) - 10} more errors")

    if imported:
        encoded_batch = urllib.parse.quote(bid or "", safe="")
        click.echo(f"\nView at: {endpoint}/traces?batch_id={encoded_batch}")
