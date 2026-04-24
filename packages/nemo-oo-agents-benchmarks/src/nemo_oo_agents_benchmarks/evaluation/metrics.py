# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Metrics calculation and reporting for benchmark evaluation.

This module provides:
- ImprovementMetrics: Key metrics for measuring self-improvement
- MetricsCalculator: Computes metrics from evaluation results
- HTML report generation for visualization
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nemo_oo_agents_benchmarks.evaluation.protocol import (
    BenchmarkReport,
    TaskResult,
)


@dataclass
class ImprovementMetrics:
    """
    Core metrics for measuring agent self-improvement capability.

    These metrics answer the key question: "Does the agent get better
    after analyzing its failures?"
    """

    # Basic success metrics
    total_tasks: int = 0
    successful_tasks: int = 0
    success_rate: float = 0.0

    # First-try vs improvement metrics (KEY!)
    first_try_success: int = 0
    first_try_success_rate: float = 0.0
    improved_after_failure: int = 0
    improvement_rate: float = 0.0  # Of failed first attempts, how many improved?

    # Iteration metrics
    avg_iterations_to_success: float = 0.0
    max_iterations_used: int = 0

    # Score improvement metrics
    avg_score_improvement: float = 0.0  # Average (final_score - initial_score)
    max_score_improvement: float = 0.0

    # Consistency metrics (tau-bench style)
    pass_at_1: float = 0.0  # Single attempt success
    pass_at_k: float = 0.0  # All k attempts succeed (if applicable)
    consistency_rate: float = 0.0  # Variance in success across attempts

    # Error analysis
    error_distribution: dict[str, int] = field(default_factory=dict)
    errors_fixed_by_iteration: dict[int, int] = field(default_factory=dict)

    # Per-benchmark breakdown
    by_benchmark: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "total_tasks": self.total_tasks,
            "successful_tasks": self.successful_tasks,
            "success_rate": self.success_rate,
            "first_try_success": self.first_try_success,
            "first_try_success_rate": self.first_try_success_rate,
            "improved_after_failure": self.improved_after_failure,
            "improvement_rate": self.improvement_rate,
            "avg_iterations_to_success": self.avg_iterations_to_success,
            "max_iterations_used": self.max_iterations_used,
            "avg_score_improvement": self.avg_score_improvement,
            "max_score_improvement": self.max_score_improvement,
            "pass_at_1": self.pass_at_1,
            "pass_at_k": self.pass_at_k,
            "consistency_rate": self.consistency_rate,
            "error_distribution": self.error_distribution,
            "errors_fixed_by_iteration": self.errors_fixed_by_iteration,
            "by_benchmark": self.by_benchmark,
        }


class MetricsCalculator:
    """
    Calculates improvement metrics from benchmark results.

    Usage:
        calculator = MetricsCalculator()
        metrics = calculator.compute_from_reports(reports)
        html = calculator.generate_html_report(metrics)
    """

    def compute_from_reports(
        self,
        reports: dict[str, BenchmarkReport],
    ) -> ImprovementMetrics:
        """
        Compute metrics from multiple benchmark reports.

        Args:
            reports: Dict mapping benchmark name to BenchmarkReport

        Returns:
            Aggregated ImprovementMetrics
        """
        metrics = ImprovementMetrics()

        all_results: list[TaskResult] = []
        by_benchmark: dict[str, dict[str, float]] = {}

        for name, report in reports.items():
            all_results.extend(report.task_results)

            # Per-benchmark metrics
            benchmark_metrics = self._compute_benchmark_metrics(report.task_results)
            by_benchmark[name] = benchmark_metrics

        metrics.by_benchmark = by_benchmark

        # Aggregate metrics
        if all_results:
            self._compute_aggregate_metrics(metrics, all_results)

        return metrics

    def compute_from_results(
        self,
        results: list[TaskResult],
        benchmark_name: str = "unknown",
    ) -> ImprovementMetrics:
        """
        Compute metrics from a list of task results.

        Args:
            results: List of TaskResult
            benchmark_name: Name of the benchmark

        Returns:
            ImprovementMetrics for this set of results
        """
        metrics = ImprovementMetrics()

        if results:
            self._compute_aggregate_metrics(metrics, results)
            metrics.by_benchmark = {benchmark_name: self._compute_benchmark_metrics(results)}

        return metrics

    def _compute_aggregate_metrics(
        self,
        metrics: ImprovementMetrics,
        results: list[TaskResult],
    ) -> None:
        """Compute aggregate metrics from results."""
        metrics.total_tasks = len(results)
        metrics.successful_tasks = sum(1 for r in results if r.final_success)
        metrics.success_rate = (
            metrics.successful_tasks / metrics.total_tasks if metrics.total_tasks > 0 else 0.0
        )

        # First-try metrics
        metrics.first_try_success = sum(1 for r in results if r.first_attempt_success)
        metrics.first_try_success_rate = (
            metrics.first_try_success / metrics.total_tasks if metrics.total_tasks > 0 else 0.0
        )

        # Improvement metrics
        failed_first = metrics.total_tasks - metrics.first_try_success
        metrics.improved_after_failure = sum(1 for r in results if r.solved_after_improvement)
        metrics.improvement_rate = (
            metrics.improved_after_failure / failed_first if failed_first > 0 else 0.0
        )

        # Iteration metrics
        successful_iterations = [r.total_iterations for r in results if r.final_success]
        metrics.avg_iterations_to_success = (
            sum(successful_iterations) / len(successful_iterations)
            if successful_iterations
            else 0.0
        )
        metrics.max_iterations_used = max((r.total_iterations for r in results), default=0)

        # Score improvement
        improvements = [r.improvement_delta for r in results if len(r.improvement_curve) > 1]
        metrics.avg_score_improvement = (
            sum(improvements) / len(improvements) if improvements else 0.0
        )
        metrics.max_score_improvement = max(improvements, default=0.0)

        # Pass@k metrics
        metrics.pass_at_1 = metrics.first_try_success_rate

        # Error distribution
        metrics.error_distribution = self._compute_error_distribution(results)

        # Errors fixed by iteration
        metrics.errors_fixed_by_iteration = self._compute_errors_fixed(results)

    def _compute_benchmark_metrics(
        self,
        results: list[TaskResult],
    ) -> dict[str, float]:
        """Compute metrics for a single benchmark."""
        if not results:
            return {}

        total = len(results)
        successful = sum(1 for r in results if r.final_success)
        first_try = sum(1 for r in results if r.first_attempt_success)
        improved = sum(1 for r in results if r.solved_after_improvement)

        improvements = [r.improvement_delta for r in results if len(r.improvement_curve) > 1]

        return {
            "total": total,
            "success_rate": successful / total if total > 0 else 0.0,
            "first_try_rate": first_try / total if total > 0 else 0.0,
            "improvement_rate": improved / (total - first_try) if (total - first_try) > 0 else 0.0,
            "avg_improvement": sum(improvements) / len(improvements) if improvements else 0.0,
        }

    def _compute_error_distribution(
        self,
        results: list[TaskResult],
    ) -> dict[str, int]:
        """Compute distribution of error categories."""
        distribution: dict[str, int] = {}

        for result in results:
            for eval_result in result.iterations:
                if eval_result.error_category:
                    cat = eval_result.error_category.value
                    distribution[cat] = distribution.get(cat, 0) + 1

        return distribution

    def _compute_errors_fixed(
        self,
        results: list[TaskResult],
    ) -> dict[int, int]:
        """Compute how many errors were fixed at each iteration."""
        fixed_by_iter: dict[int, int] = {}

        for result in results:
            if result.solved_after_improvement:
                # Find the iteration where it succeeded
                for i, eval_result in enumerate(result.iterations):
                    if eval_result.success:
                        iter_num = i + 1
                        fixed_by_iter[iter_num] = fixed_by_iter.get(iter_num, 0) + 1
                        break

        return fixed_by_iter

    def generate_html_report(
        self,
        metrics: ImprovementMetrics,
        reports: dict[str, BenchmarkReport] | None = None,
        output_path: str | None = None,
    ) -> str:
        """
        Generate an HTML report from metrics.

        Args:
            metrics: Computed metrics
            reports: Optional benchmark reports for detailed info
            output_path: Optional path to save HTML file

        Returns:
            HTML string
        """
        html = self._build_html_report(metrics, reports)

        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                f.write(html)

        return html

    def _build_html_report(
        self,
        metrics: ImprovementMetrics,
        reports: dict[str, BenchmarkReport] | None,
    ) -> str:
        """Build HTML report content."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Build summary cards
        summary_cards = f"""
        <div class="summary-grid">
            <div class="card success">
                <div class="card-value">{metrics.success_rate:.1%}</div>
                <div class="card-label">Overall Success Rate</div>
            </div>
            <div class="card improvement">
                <div class="card-value">{metrics.improvement_rate:.1%}</div>
                <div class="card-label">Improvement Rate</div>
                <div class="card-detail">Of failed first attempts</div>
            </div>
            <div class="card first-try">
                <div class="card-value">{metrics.first_try_success_rate:.1%}</div>
                <div class="card-label">First-Try Success</div>
            </div>
            <div class="card iterations">
                <div class="card-value">{metrics.avg_iterations_to_success:.1f}</div>
                <div class="card-label">Avg Iterations to Success</div>
            </div>
        </div>
        """

        # Build benchmark table
        benchmark_rows = ""
        for name, bench_metrics in metrics.by_benchmark.items():
            benchmark_rows += f"""
            <tr>
                <td>{name}</td>
                <td>{bench_metrics.get("total", 0)}</td>
                <td>{bench_metrics.get("success_rate", 0):.1%}</td>
                <td>{bench_metrics.get("first_try_rate", 0):.1%}</td>
                <td class="highlight">{bench_metrics.get("improvement_rate", 0):.1%}</td>
                <td>{bench_metrics.get("avg_improvement", 0):.3f}</td>
            </tr>
            """

        benchmark_table = f"""
        <table class="metrics-table">
            <thead>
                <tr>
                    <th>Benchmark</th>
                    <th>Tasks</th>
                    <th>Success Rate</th>
                    <th>First-Try</th>
                    <th>Improvement Rate</th>
                    <th>Avg Score Improvement</th>
                </tr>
            </thead>
            <tbody>
                {benchmark_rows}
            </tbody>
        </table>
        """

        # Build error distribution chart data
        error_labels = list(metrics.error_distribution.keys())
        error_values = list(metrics.error_distribution.values())

        # Build errors fixed chart data
        fixed_labels = [f"Iter {i}" for i in sorted(metrics.errors_fixed_by_iteration.keys())]
        fixed_values = [
            metrics.errors_fixed_by_iteration[i]
            for i in sorted(metrics.errors_fixed_by_iteration.keys())
        ]

        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agent Self-Improvement Evaluation Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --primary: #2563eb;
            --success: #16a34a;
            --warning: #ca8a04;
            --danger: #dc2626;
            --bg: #f8fafc;
            --card-bg: #ffffff;
            --text: #1e293b;
            --text-light: #64748b;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2rem;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        header {{
            margin-bottom: 2rem;
        }}

        h1 {{
            font-size: 1.875rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }}

        .timestamp {{
            color: var(--text-light);
            font-size: 0.875rem;
        }}

        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}

        .card {{
            background: var(--card-bg);
            border-radius: 0.75rem;
            padding: 1.5rem;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
        }}

        .card-value {{
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.25rem;
        }}

        .card-label {{
            font-size: 0.875rem;
            color: var(--text-light);
        }}

        .card-detail {{
            font-size: 0.75rem;
            color: var(--text-light);
            margin-top: 0.25rem;
        }}

        .card.success .card-value {{ color: var(--success); }}
        .card.improvement .card-value {{ color: var(--primary); }}
        .card.first-try .card-value {{ color: var(--warning); }}
        .card.iterations .card-value {{ color: var(--text); }}

        section {{
            background: var(--card-bg);
            border-radius: 0.75rem;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
        }}

        section h2 {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid #e2e8f0;
        }}

        .metrics-table {{
            width: 100%;
            border-collapse: collapse;
        }}

        .metrics-table th,
        .metrics-table td {{
            padding: 0.75rem 1rem;
            text-align: left;
            border-bottom: 1px solid #e2e8f0;
        }}

        .metrics-table th {{
            background: #f1f5f9;
            font-weight: 600;
            font-size: 0.875rem;
        }}

        .metrics-table td.highlight {{
            font-weight: 600;
            color: var(--primary);
        }}

        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 1.5rem;
        }}

        .chart-container {{
            position: relative;
            height: 300px;
        }}

        .key-insight {{
            background: #eff6ff;
            border-left: 4px solid var(--primary);
            padding: 1rem;
            margin-top: 1rem;
            border-radius: 0 0.5rem 0.5rem 0;
        }}

        .key-insight-title {{
            font-weight: 600;
            margin-bottom: 0.5rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Agent Self-Improvement Evaluation Report</h1>
            <div class="timestamp">Generated: {timestamp}</div>
        </header>

        {summary_cards}

        <section>
            <h2>Benchmark Results</h2>
            {benchmark_table}

            <div class="key-insight">
                <div class="key-insight-title">Key Insight</div>
                <p>
                    The <strong>Improvement Rate</strong> ({metrics.improvement_rate:.1%}) shows how often
                    the agent successfully solved a task after initially failing, by analyzing its traces
                    and refining its approach. Higher is better.
                </p>
            </div>
        </section>

        <section>
            <h2>Analysis</h2>
            <div class="charts-grid">
                <div>
                    <h3>Error Distribution</h3>
                    <div class="chart-container">
                        <canvas id="errorChart"></canvas>
                    </div>
                </div>
                <div>
                    <h3>Errors Fixed by Iteration</h3>
                    <div class="chart-container">
                        <canvas id="fixedChart"></canvas>
                    </div>
                </div>
            </div>
        </section>

        <section>
            <h2>Detailed Metrics</h2>
            <table class="metrics-table">
                <tr>
                    <td>Total Tasks</td>
                    <td><strong>{metrics.total_tasks}</strong></td>
                </tr>
                <tr>
                    <td>Successful Tasks</td>
                    <td><strong>{metrics.successful_tasks}</strong></td>
                </tr>
                <tr>
                    <td>First-Try Successes</td>
                    <td><strong>{metrics.first_try_success}</strong></td>
                </tr>
                <tr>
                    <td>Improved After Failure</td>
                    <td><strong>{metrics.improved_after_failure}</strong></td>
                </tr>
                <tr>
                    <td>Average Score Improvement</td>
                    <td><strong>{metrics.avg_score_improvement:.4f}</strong></td>
                </tr>
                <tr>
                    <td>Max Score Improvement</td>
                    <td><strong>{metrics.max_score_improvement:.4f}</strong></td>
                </tr>
                <tr>
                    <td>Max Iterations Used</td>
                    <td><strong>{metrics.max_iterations_used}</strong></td>
                </tr>
            </table>
        </section>
    </div>

    <script>
        // Error distribution chart
        new Chart(document.getElementById('errorChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(error_labels)},
                datasets: [{{
                    data: {json.dumps(error_values)},
                    backgroundColor: [
                        '#ef4444', '#f97316', '#eab308', '#22c55e',
                        '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899'
                    ]
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'right'
                    }}
                }}
            }}
        }});

        // Errors fixed by iteration chart
        new Chart(document.getElementById('fixedChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(fixed_labels)},
                datasets: [{{
                    label: 'Errors Fixed',
                    data: {json.dumps(fixed_values)},
                    backgroundColor: '#3b82f6'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        display: false
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        ticks: {{
                            stepSize: 1
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
        """

        return html

    def save_metrics_json(
        self,
        metrics: ImprovementMetrics,
        output_path: str,
    ) -> None:
        """Save metrics to JSON file."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(metrics.to_dict(), f, indent=2)
