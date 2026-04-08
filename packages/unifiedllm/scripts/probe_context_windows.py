#!/usr/bin/env python3
"""Probe models to discover their actual context window sizes.

This script sends oversized requests to each model and parses the error
message to extract the actual context window. This is the only reliable
way to discover context windows since most APIs don't expose this info.

Usage:
    python probe_context_windows.py
    python probe_context_windows.py --model nvidia/openai/gpt-oss-120b
    python probe_context_windows.py --update-registry
"""

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unifiedllm import MODELS, CompletionClient


def load_models_yaml() -> dict:
    """Load models from util/config/models.yaml."""
    # Find project root (look for util/config/models.yaml)
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "util" / "config" / "models.yaml").exists():
            yaml_path = parent / "util" / "config" / "models.yaml"
            break
    else:
        raise FileNotFoundError("Could not find util/config/models.yaml")

    with open(yaml_path) as f:
        return yaml.safe_load(f)


def generate_large_message(target_tokens: int) -> str:
    """Generate a message with approximately target_tokens tokens.

    Uses 'a ' repeated since it's ~1 token per 'a '.
    """
    # Each 'a ' is roughly 1 token
    return "a " * target_tokens


def extract_context_from_error(error_msg: str, probe_size: int = 500_000) -> int | None:
    """Extract context window size from error message.

    Different providers return different error formats:
    - OpenAI: "maximum context length is 128000 tokens"
    - Anthropic: "maximum number of tokens is 200000"
    - NVIDIA: "max_tokens must be at least 1, got -X" (we can calculate from X)
    """
    # Direct patterns that mention the context window
    patterns = [
        r"maximum context length is (\d+)",
        r"context length (?:is |of )?(\d+)",
        r"model's context length \((\d+)",  # "model's context length (262144 tokens)"
        r"max(?:imum)? (?:input )?tokens?[:\s]+(\d+)",
        r"context window[:\s]+(\d+)",
        r"(\d+) tokens? (?:max|limit)",
        r"limit(?:ed)? to (\d+)",
        r"model.s maximum context length is (\d+)",
        r"This model supports at most (\d+)",
        r"configured limit of (\d+) tokens",  # Azure: "configured limit of 272000 tokens"
        r"limit of (\d+) tokens",
    ]

    for pattern in patterns:
        match = re.search(pattern, error_msg.lower())
        if match:
            return int(match.group(1))

    # Special case: "max_tokens must be at least 1, got -X"
    # This means: context_window - probe_size = -X
    # So: context_window = probe_size - X
    negative_pattern = r"max_tokens must be at least \d+, got -(\d+)"
    match = re.search(negative_pattern, error_msg)
    if match:
        negative_tokens = int(match.group(1))
        # context_window = probe_size - negative_tokens
        # But we need to account for the max_tokens=1 we requested
        return probe_size - negative_tokens + 1

    return None


def probe_model(model_name: str, model_config: dict, verbose: bool = True) -> dict:
    """Probe a model to discover its context window.

    Args:
        model_name: The model identifier
        model_config: Config from models.yaml (endpoint, api_key_env, etc.)
        verbose: Print progress

    Returns:
        dict with keys:
            - model: model name
            - claimed: context window from registry (if any)
            - actual: discovered context window (or None)
            - error: error message (if failed to probe)
    """
    result = {
        "model": model_name,
        "claimed": MODELS.get(model_name, {}).get("context_window") if model_name in MODELS else None,
        "actual": None,
        "error": None,
    }

    # Start with a large request that should exceed most context windows
    probe_size = 500_000  # 500k tokens should exceed almost everything

    try:
        # Build client from models.yaml config
        api_key_env = model_config.get("api_key_env", "NVIDIA_INTERNAL_API_KEY")
        api_key = os.getenv(api_key_env)

        if not api_key:
            result["error"] = f"Missing env var: {api_key_env}"
            if verbose:
                print(f"  SKIP: {api_key_env} not set")
            return result

        # For custom endpoints, litellm needs "openai/" prefix to know the format
        # The models.yaml uses names like "nvidia/openai/gpt-oss-120b" but
        # litellm needs "openai/nvidia/openai/gpt-oss-120b"
        litellm_model = model_name
        if model_config.get("endpoint") and not model_name.startswith("openai/"):
            litellm_model = f"openai/{model_name}"

        llm = CompletionClient(
            model=litellm_model,
            api_base=model_config.get("endpoint"),
            api_key=api_key,
        )

        if verbose:
            print(f"Probing {model_name} with {probe_size:,} tokens...")

        # Generate oversized message
        large_content = generate_large_message(probe_size)
        messages = [{"role": "user", "content": large_content}]

        # This should fail with context exceeded error
        llm.call(messages, max_tokens=1)

        # If we get here, the model accepted the request (unlikely but possible)
        result["actual"] = f">={probe_size}"
        if verbose:
            print(f"  WARNING: Model accepted {probe_size:,} tokens!")

    except Exception as e:
        error_msg = str(e)

        # Try to extract context window from error
        actual = extract_context_from_error(error_msg, probe_size)

        if actual:
            result["actual"] = actual
            if verbose:
                claimed = result["claimed"]
                status = ""
                if claimed:
                    status = " (matches registry)" if actual == claimed else f" (registry says {claimed:,})"
                print(f"  Context window: {actual:,}{status}")
        else:
            result["error"] = error_msg[:300]  # Truncate long errors
            if verbose:
                print("  Could not extract context window")
                print(f"  Error: {error_msg[:300]}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Probe models for context window sizes")
    parser.add_argument("--model", "-m", help="Specific model to probe")
    parser.add_argument("--update-registry", action="store_true", help="Print updated registry entries")
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    args = parser.parse_args()

    # Load models from YAML
    yaml_config = load_models_yaml()
    yaml_models = yaml_config.get("models", {})

    print(f"Loaded {len(yaml_models)} models from models.yaml\n")

    results = []

    if args.model:
        # Probe single model
        if args.model in yaml_models:
            models_to_probe = [(args.model, yaml_models[args.model])]
        else:
            print(f"Model {args.model} not found in models.yaml")
            print(f"Available models: {list(yaml_models.keys())[:5]}...")
            return
    else:
        # Probe all models from YAML
        models_to_probe = list(yaml_models.items())

    print(f"Probing {len(models_to_probe)} model(s)...\n")

    for model_name, model_config in models_to_probe:
        result = probe_model(model_name, model_config, verbose=not args.quiet)
        results.append(result)
        if not args.quiet:
            print()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for r in results:
        model = r["model"]
        claimed = r["claimed"]
        actual = r["actual"]

        if r.get("error") and "Missing env var" in r["error"]:
            status = "SKIPPED (no API key)"
        elif actual is None:
            status = "FAILED"
        elif isinstance(actual, str):  # e.g., ">=500000"
            status = actual
        elif claimed is None:
            status = f"NEW: {actual:,}"
        elif actual == claimed:
            status = f"OK: {actual:,}"
        else:
            status = f"MISMATCH: claimed={claimed:,}, actual={actual:,}"

        print(f"  {model}: {status}")

    # Generate updated registry if requested
    if args.update_registry:
        print("\n" + "=" * 70)
        print("UPDATED REGISTRY ENTRIES (copy to registry.py)")
        print("=" * 70)
        print()

        for r in results:
            if r["actual"] and isinstance(r["actual"], int):
                model = r["model"]
                actual = r["actual"]
                yaml_config = yaml_models.get(model, {})

                endpoint = yaml_config.get("endpoint", "https://inference-api.nvidia.com/v1")
                api_key_env = yaml_config.get("api_key_env", "NVIDIA_INTERNAL_API_KEY")

                print(f'    "{model}": ModelConfig(')
                print(f"        context_window={actual:_},")
                print(f'        api_base="{endpoint}",')
                print(f'        api_key_env="{api_key_env}",')
                print("    ),")


if __name__ == "__main__":
    main()
