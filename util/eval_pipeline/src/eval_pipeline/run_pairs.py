#!/usr/bin/env python
"""Run evaluation for specific (task_id, model_id) pairs.

This script enables sparse evaluation - running only specific (task, model) combinations
instead of the full Cartesian product.

Usage:
    python -m eval_pipeline.run_pairs \
        --config config.yaml \
        --pairs "router_transform_002:nemotron3-nano-30b,calculate_single_005:gemini-2.5-flash-lite" \
        --runs 9 \
        --parallel 10 \
        --output-dir results/proposed_eval

The pairs format is: task_id:model_id,task_id:model_id,...
"""

import argparse
import asyncio
import sys
from pathlib import Path


def parse_pairs(pairs_str: str) -> list[tuple[str, str]]:
    """Parse pairs string into list of (task_id, model_id) tuples."""
    pairs = []
    for pair in pairs_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(f"Invalid pair format: {pair}. Expected task_id:model_id")
        task_id, model_id = pair.split(":", 1)
        pairs.append((task_id.strip(), model_id.strip()))
    return pairs


def load_custom_strategy(module_path: str, class_name: str):
    """Load a custom strategy class from a module.

    Args:
        module_path: Module path (e.g., "agents.strategy" or just "strategy")
        class_name: Class name (e.g., "MyCustomStrategy")

    Returns:
        Strategy instance
    """
    import importlib

    module = importlib.import_module(module_path)
    strategy_class = getattr(module, class_name)
    return strategy_class()


async def main():
    parser = argparse.ArgumentParser(description="Run eval for specific (task, model) pairs")
    parser.add_argument("--config", "-c", required=True, help="Path to config.yaml")
    parser.add_argument(
        "--pairs",
        "-p",
        required=True,
        help="Pairs to run: task_id:model_id,task_id:model_id,...",
    )
    parser.add_argument("--runs", "-r", type=int, default=1, help="Runs per pair")
    parser.add_argument("--parallel", type=int, default=1, help="Max concurrent samples")
    parser.add_argument("--output-dir", "-o", help="Output directory (default: from config)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    parser.add_argument("--name", "-n", help="Experiment name override")

    args = parser.parse_args()

    # Parse pairs
    pairs = parse_pairs(args.pairs)
    if not pairs:
        print("No pairs specified", file=sys.stderr)
        sys.exit(1)

    # Load config to get default_strategy (before creating evaluator)
    from nemo_oo_agents import set_default_strategy
    from eval_pipeline.config import StrategyConfig, load_config

    config = load_config(args.config)

    # Apply default strategy from config if specified
    if config.default_strategy is not None:
        if isinstance(config.default_strategy, str):
            # Built-in strategy name
            from eval_pipeline.cli import get_strategy_instance

            strategy_instance = get_strategy_instance(config.default_strategy)
            set_default_strategy(strategy_instance)
            if not args.quiet:
                print(f"Using strategy: {config.default_strategy}")
        elif isinstance(config.default_strategy, StrategyConfig):
            # Custom strategy class from module
            strategy_instance = load_custom_strategy(
                config.default_strategy.module,
                config.default_strategy.class_name,
            )
            set_default_strategy(strategy_instance)
            if not args.quiet:
                print(
                    f"Using strategy: {config.default_strategy.module}.{config.default_strategy.class_name}"
                )

    # Load evaluator from config
    from eval_pipeline import Evaluator

    evaluator = Evaluator.from_config(args.config)

    # Override name if specified
    if args.name:
        evaluator.name = args.name

    # Override output dir if specified
    if args.output_dir:
        evaluator.output_dir = Path(args.output_dir)

    # Build samples for specific pairs
    try:
        samples = evaluator.build_samples_for_pairs(pairs, runs=args.runs)
    except ValueError as e:
        print(f"Error building samples: {e}", file=sys.stderr)
        sys.exit(1)

    if not samples:
        print("No samples to run", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Running {len(samples)} samples for {len(pairs)} pairs × {args.runs} runs...")

    # Run samples
    results = await evaluator.run_samples(
        samples,
        parallel=args.parallel,
        quiet=args.quiet,
    )

    # Output summary
    if not args.quiet:
        print(f"\n{results.summary()}")
        print(f"Results: {results.output_file}")

    # Always exit 0 - the caller should parse the results file to determine success
    # (Exit code 1 was causing the optimizer to incorrectly skip result parsing)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
