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
    post_trace,
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


def _trial_meta(jsonl_path: Path) -> dict:
    """Extract Harbor metadata for a trace file from its surrounding directory structure.

    Expected layout::

        <job_dir>/
            result.json              ← job-level (stats.evals for experiment name)
            <trial_name>/
                result.json          ← trial_name, task_name, agent_info
                verifier/
                    reward.json      ← {"score": <float>}  (or reward.txt)
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

    # reward.json → {"score": <float>}  OR  reward.txt → plain float string
    score: float | None = None
    reward_json = trial_dir / "verifier" / "reward.json"
    reward_txt = trial_dir / "verifier" / "reward.txt"
    if reward_json.exists():
        score = _read_json(reward_json).get("score")
    elif reward_txt.exists():
        try:
            score = float(reward_txt.read_text().strip())
        except ValueError:
            pass

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
def command(path: str, endpoint: str, experiment: str | None, batch_id: str | None):
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

        file_imported = False

        with open(jsonl_path) as f:
            for line_num, raw_line in enumerate(f, 1):
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

                if post_trace(endpoint, body):
                    file_imported = True
                else:
                    errors.append(f"{jsonl_path.name}:{line_num}: failed to post")

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
