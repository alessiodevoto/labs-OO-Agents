"""Example usage of BenchmarkContextProvider for different benchmarks.

This shows how to configure the optimizer for different benchmarks with
benchmark-specific context.
"""

import asyncio
from pathlib import Path

from e2e_optimization import (
    CapabilityContextProvider,
    DABStepContextProvider,
    Optimizer,
    TauBenchContextProvider,
)


async def optimize_capability_tests():
    """Optimize capability tests using default CapabilityContextProvider."""

    # Option 1: Use default (automatically uses CapabilityContextProvider)
    optimizer = Optimizer(
        config_path="experiments/capability_eval/config.yaml",
    )

    # Option 2: Explicitly specify (same result)
    optimizer = Optimizer(
        config_path="experiments/capability_eval/config.yaml",
        context_provider=CapabilityContextProvider(),
    )

    # Run one iteration
    result = await optimizer.run_iteration()
    print(f"Capability optimization complete: {result}")


async def optimize_dabstep():
    """Optimize DABStep agent with solution docs and manual.md context."""

    # Configure DABStep context provider
    context_provider = DABStepContextProvider(
        solutions_dir=Path("experiments/dabstep_solutions"),
        manual_path=Path.home() / ".cache/dabstep/data/context/manual.md",
        load_full_manual=False,  # Use preview, not full manual (saves tokens)
    )

    optimizer = Optimizer(
        config_path="experiments/dabstep_eval/config.yaml",
        context_provider=context_provider,
    )

    # Run one iteration
    result = await optimizer.run_iteration()
    print(f"DABStep optimization complete: {result}")


async def optimize_tau_bench():
    """Optimize TAU-bench agent with domain API context."""

    # Configure TAU-bench context provider
    context_provider = TauBenchContextProvider()

    optimizer = Optimizer(
        config_path="experiments/tau_bench_eval/config.yaml",
        context_provider=context_provider,
    )

    # Run one iteration
    result = await optimizer.run_iteration()
    print(f"TAU-bench optimization complete: {result}")


async def custom_context_provider_example():
    """Example: Create a custom context provider for a new benchmark."""

    from e2e_optimization import BenchmarkContextProvider

    class MyBenchmarkContextProvider(BenchmarkContextProvider):
        """Custom context for my benchmark."""

        def __init__(self, reference_docs_dir: Path):
            self.reference_docs_dir = reference_docs_dir

        def get_reflection_context(self, task_id: str | None = None) -> str:
            parts = [
                "## Benchmark: My Custom Benchmark",
                "",
                "This benchmark tests XYZ capabilities.",
                "",
                "### Domain Knowledge",
                "- Rule 1: ...",
                "- Rule 2: ...",
                "",
            ]

            # Add task-specific context if available
            if task_id and self.reference_docs_dir:
                ref_doc = self.reference_docs_dir / f"task_{task_id}_reference.md"
                if ref_doc.exists():
                    parts.extend(
                        [
                            f"### Reference Solution for Task {task_id}",
                            "",
                            ref_doc.read_text(),
                            "",
                        ]
                    )

            return "\n".join(parts)

    # Use custom provider
    optimizer = Optimizer(
        config_path="experiments/my_benchmark/config.yaml",
        context_provider=MyBenchmarkContextProvider(
            reference_docs_dir=Path("experiments/my_benchmark/references")
        ),
    )

    result = await optimizer.run_iteration()
    print(f"My benchmark optimization complete: {result}")


async def step_by_step_example():
    """Example: Run optimization step-by-step for debugging."""

    optimizer = Optimizer(
        config_path="experiments/capability_eval/config.yaml",
        context_provider=CapabilityContextProvider(),
    )

    # Step 1: Run evaluation
    print("Step 1: Running evaluation...")
    await optimizer.run_eval(n_runs=3)

    # Step 2: Analyze consistency
    print("Step 2: Analyzing consistency...")
    await optimizer.analyze(n_samples=3)

    # Step 3: Reflect (with retry loop)
    print("Step 3: Reflecting...")
    acceptance_result = await optimizer.reflect_loop(max_attempts=3)

    if acceptance_result.accepted:
        print("✅ Proposed changes accepted!")
        print(f"   Parent: {acceptance_result.parent_pass_rate:.1%}")
        print(f"   Proposed: {acceptance_result.proposed_pass_rate:.1%}")
    else:
        print("❌ All attempts rejected")
        print(f"   Best proposed: {acceptance_result.proposed_pass_rate:.1%}")
        print(f"   Parent: {acceptance_result.parent_pass_rate:.1%}")


# Registry-based loading (alternative approach)
def using_registry():
    """Example: Use get_context_provider() for dynamic loading."""

    from e2e_optimization import get_context_provider

    # For capability tests (minimal config needed)
    provider = get_context_provider("capability")

    # For DABStep (with kwargs)
    provider = get_context_provider(
        "dabstep",
        solutions_dir="experiments/dabstep_solutions",
        manual_path="~/.cache/dabstep/data/context/manual.md",
    )

    # For TAU-bench
    provider = get_context_provider("tau_bench")

    # Use in optimizer
    _ = Optimizer(config_path="config.yaml", context_provider=provider)


if __name__ == "__main__":
    # Run one of the examples
    asyncio.run(optimize_capability_tests())
    # asyncio.run(optimize_dabstep())
    # asyncio.run(optimize_tau_bench())
    # asyncio.run(step_by_step_example())
