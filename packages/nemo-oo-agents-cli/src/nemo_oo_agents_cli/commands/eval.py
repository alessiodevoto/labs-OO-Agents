# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run an evaluation pipeline job.

Thin passthrough to `python -m eval_pipeline`. All arguments are forwarded
as-is, so this command always stays in sync with eval_pipeline's own CLI.

Usage:
    nemo-oo eval --config config.yaml
    nemo-oo eval --config config.yaml --runs 3 --parallel 10
    nemo-oo eval --config config.yaml --test sentiment --limit 5
"""

import click


@click.command(
    add_help_option=False,
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    },
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def command(ctx: click.Context, args: tuple[str, ...]):
    """Run an evaluation pipeline (passthrough to eval_pipeline).

    All arguments are forwarded directly to eval_pipeline's CLI.

    \b
    Examples:
        nemo-oo eval --config config.yaml
        nemo-oo eval --config config.yaml --runs 3 --parallel 10
        nemo-oo eval --config config.yaml --test sentiment --limit 5
        nemo-oo eval --config config.yaml --models gpt-4,claude-3 -q
        nemo-oo eval --config config.yaml --default_strategy codeact
        nemo-oo eval --help
    """
    import asyncio
    import faulthandler
    import signal
    import sys

    # Enable faulthandler (same as eval_pipeline.__main__)
    faulthandler.enable()
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)

    # Forward all arguments directly to eval_pipeline
    old_argv = sys.argv
    sys.argv = ["eval_pipeline", *args]
    try:
        from eval_pipeline.cli import main_async

        asyncio.run(main_async())
    finally:
        sys.argv = old_argv
