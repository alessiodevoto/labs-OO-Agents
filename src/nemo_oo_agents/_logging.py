"""Convenience helpers for configuring nemo_oo_agents logging.

Library code uses ``logging.getLogger(__name__)`` throughout, producing a
hierarchy rooted at ``nemo_oo_agents``.  Applications that want to see what the
library is doing can call :func:`enable_logging` instead of wiring up
handlers manually::

    from nemo_oo_agents import enable_logging

    # Everything at DEBUG
    enable_logging()

    # Only strategy decisions
    enable_logging(name="nemo_oo_agents.strategies")

    # INFO-level overview
    enable_logging(level=logging.INFO)

The function is intentionally minimal — it adds a single
:class:`~logging.StreamHandler` with a readable format.  For production
use, configure logging via :func:`logging.config.dictConfig` as usual.

Logger hierarchy (all children of ``nemo_oo_agents``)::

    nemo_oo_agents.agent               Agent lifecycle & configuration
    nemo_oo_agents.runtime.actor       Execution engine
    nemo_oo_agents.runtime.hooks       Hook dispatch
    nemo_oo_agents.runtime.code_validator  Code validation / safety checks
    nemo_oo_agents.strategies.codeact  CodeAct strategy
    nemo_oo_agents.strategies.predict  Predict strategy
    nemo_oo_agents.strategies.pure_python  PurePython strategy
    nemo_oo_agents.strategies.reflexion  Reflexion strategy
    nemo_oo_agents.tools.*             Individual tool modules
    nemo_oo_agents.storage.*           Storage backends
    nemo_oo_agents.library_manager     Library/skill loading
    nemo_oo_agents.skill_manager       Skill discovery
"""

from __future__ import annotations

import logging
import sys
from typing import IO

_DEFAULT_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DEFAULT_DATE_FORMAT = "%H:%M:%S"


def enable_logging(
    level: int = logging.DEBUG,
    name: str = "nemo_oo_agents",
    fmt: str = _DEFAULT_FORMAT,
    datefmt: str = _DEFAULT_DATE_FORMAT,
    stream: IO[str] | None = None,
) -> None:
    """Attach a :class:`~logging.StreamHandler` to an *nemo_oo_agents* logger.

    This is a convenience for interactive / development use.  It is safe to
    call multiple times — duplicate handlers are not added, but the log
    level is always updated.  Format changes on repeated calls require
    removing the handler first.

    Parameters
    ----------
    level:
        Log level to set on the target logger (default ``DEBUG``).
    name:
        Logger name to configure.  Defaults to the library root
        (``"nemo_oo_agents"``).  Pass a child name to narrow the scope, e.g.
        ``"nemo_oo_agents.strategies"`` or ``"nemo_oo_agents.runtime.actor"``.
    fmt:
        Log format string.
    datefmt:
        Date format string.
    stream:
        Output stream (default ``sys.stderr``).
    """
    target = logging.getLogger(name)

    if stream is None:
        stream = sys.stderr

    # Avoid adding duplicate handlers on repeated calls.
    # Use exact type check — NullHandler inherits from Handler (not
    # StreamHandler in practice, but guard defensively) and has no .stream.
    for h in target.handlers:
        if type(h) is logging.StreamHandler and h.stream is stream:
            target.setLevel(level)
            return

    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    target.addHandler(handler)
    target.setLevel(level)
