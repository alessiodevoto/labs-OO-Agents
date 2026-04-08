"""Metadata capture for reproducibility and tracing context.

Captures git state, environment info, and framework version to attach to traces
as OpenTelemetry Resource Attributes.
"""

import platform
import subprocess


def get_git_metadata() -> dict[str, str]:
    """Capture git state at trace initialization.

    Returns:
        Dictionary with git metadata:
        - git.commit: Short commit hash (7 chars)
        - git.commit_full: Full commit hash
        - git.dirty: "true" if uncommitted changes, "false" otherwise
        - git.branch: Current branch name

        Returns empty dict if not in a git repository or git is not available.
    """
    try:
        # Get commit hash
        commit = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

        # Check for uncommitted changes
        # git diff --quiet returns 0 if clean, 1 if dirty
        dirty = subprocess.call(["git", "diff", "--quiet"], stderr=subprocess.DEVNULL) != 0

        # Get branch name
        branch = (
            subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )

        return {
            "git.commit": commit[:7],
            "git.commit_full": commit,
            "git.dirty": str(dirty).lower(),
            "git.branch": branch,
        }
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not in a git repo or git not installed
        return {}


def get_environment_metadata() -> dict[str, str]:
    """Capture environment information.

    Returns:
        Dictionary with environment metadata:
        - agent006.version: Framework version
        - python.version: Python version
        - hostname: Machine hostname
    """
    # Import here to avoid circular import
    from openinference_instrumentation_agent006 import __version__

    return {
        "agent006.version": __version__,
        "python.version": platform.python_version(),
        "hostname": platform.node(),
    }


def get_all_metadata() -> dict[str, str]:
    """Get all metadata (git + environment).

    Returns:
        Combined dictionary with all metadata attributes.
    """
    metadata = {}
    metadata.update(get_git_metadata())
    metadata.update(get_environment_metadata())
    return metadata
