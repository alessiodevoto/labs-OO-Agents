#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify context window values by testing at boundaries.

For models where we looked up the value from docs (not probed), this script
verifies by sending requests at the documented limit.

Usage:
    python verify_context_windows.py
"""

import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unifiedllm import CompletionClient


def load_models_yaml() -> dict:
    """Load models from util/config/models.yaml."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "util" / "config" / "models.yaml").exists():
            yaml_path = parent / "util" / "config" / "models.yaml"
            break
    else:
        raise FileNotFoundError("Could not find util/config/models.yaml")

    with open(yaml_path) as f:
        return yaml.safe_load(f)


def generate_tokens(count: int) -> str:
    """Generate approximately `count` tokens using 'a ' pairs."""
    return "a " * count


def test_at_limit(model_name: str, model_config: dict, context_window: int) -> dict:
    """Test a model at the documented context window boundary.

    Returns dict with:
        - model: model name
        - context_window: documented value
        - under_limit: result of request at limit - 1000
        - at_limit: result of request at exactly limit
        - over_limit: result of request at limit + 1000
    """
    result = {
        "model": model_name,
        "context_window": context_window,
        "under_limit": None,
        "at_limit": None,
        "over_limit": None,
    }

    api_key_env = model_config.get("api_key_env", "NVIDIA_INTERNAL_API_KEY")
    api_key = os.getenv(api_key_env)

    if not api_key:
        result["error"] = f"Missing {api_key_env}"
        return result

    litellm_model = model_name
    if model_config.get("endpoint") and not model_name.startswith("openai/"):
        litellm_model = f"openai/{model_name}"

    llm = CompletionClient(
        model=litellm_model,
        api_base=model_config.get("endpoint"),
        api_key=api_key,
    )

    # Test cases: under, at, and over the limit
    # Use a buffer for system prompt overhead
    buffer = 1000
    test_cases = [
        ("under_limit", context_window - buffer - 100),  # Should succeed
        ("at_limit", context_window - buffer),  # Should succeed (barely)
        ("over_limit", context_window + 100),  # Should fail
    ]

    for name, token_count in test_cases:
        print(f"  Testing {name} ({token_count:,} tokens)...", end=" ", flush=True)

        try:
            content = generate_tokens(token_count)
            messages = [{"role": "user", "content": content}]
            llm.call(messages, max_tokens=1)
            result[name] = "SUCCESS"
            print("SUCCESS")
        except Exception as e:
            error = str(e)
            if "context" in error.lower() or "too long" in error.lower() or "limit" in error.lower():
                result[name] = "CONTEXT_EXCEEDED"
                print("CONTEXT_EXCEEDED (expected for over_limit)")
            else:
                result[name] = f"ERROR: {error[:100]}"
                print(f"ERROR: {error[:100]}")

    return result


def main():
    yaml_config = load_models_yaml()
    yaml_models = yaml_config.get("models", {})

    # Models we need to verify (looked up from docs, not probed)
    models_to_verify = {
        # Anthropic models - documented as 200K
        "aws/anthropic/claude-opus-4-5": 200_000,
        "aws/anthropic/claude-haiku-4-5-v1": 200_000,
        "aws/anthropic/bedrock-claude-sonnet-4-5-v1": 200_000,
        # Gemini models - documented as 1M (skip for now, too large)
        # "gcp/google/gemini-2.5-pro": 1_000_000,
    }

    print("Verifying context windows for models with looked-up values\n")
    print("=" * 70)

    results = []
    for model_name, context_window in models_to_verify.items():
        if model_name not in yaml_models:
            print(f"SKIP: {model_name} not in models.yaml")
            continue

        print(f"\n{model_name} (documented: {context_window:,} tokens)")
        print("-" * 50)

        result = test_at_limit(model_name, yaml_models[model_name], context_window)
        results.append(result)

    # Summary
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)

    for r in results:
        model = r["model"]
        under = r.get("under_limit", "N/A")
        at = r.get("at_limit", "N/A")
        over = r.get("over_limit", "N/A")

        # Expected: under=SUCCESS, at=SUCCESS, over=CONTEXT_EXCEEDED
        if under == "SUCCESS" and over == "CONTEXT_EXCEEDED":
            status = "VERIFIED"
        elif r.get("error"):
            status = f"SKIP ({r['error']})"
        else:
            status = "UNEXPECTED"

        print(f"  {model}: {status}")
        print(f"    under={under}, at={at}, over={over}")


if __name__ == "__main__":
    main()
