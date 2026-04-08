#!/usr/bin/env python3
"""Debug LLM request failures by systematically simplifying the request.

Usage:
    # Test the original failing request
    python debug_request.py eval_errors/llm_errors.jsonl

    # Test with modifications
    python debug_request.py eval_errors/llm_errors.jsonl --remove-messages 2
    python debug_request.py eval_errors/llm_errors.jsonl --max-tokens 4096
    python debug_request.py eval_errors/llm_errors.jsonl --no-tool-choice
"""

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


async def test_request(
    request_data: dict, modifications: dict | None = None, verbose: bool = True
) -> tuple[bool, dict]:
    """Test a request with optional modifications.

    Args:
        request_data: The request data from JSONL
        modifications: Dict of modifications to apply
        verbose: Print details

    Returns:
        (success: bool, response: dict)
    """
    try:
        import httpx
    except ImportError as e:
        raise ImportError("httpx is required. Install it with: pip install httpx") from e

    # Deep copy to avoid modifying original
    request = deepcopy(request_data)
    body = request["body"]

    # Apply modifications
    if modifications:
        if "max_tokens" in modifications:
            body["max_tokens"] = modifications["max_tokens"]
            if verbose:
                print(f"  → Modified max_tokens: {modifications['max_tokens']}")

        if "remove_messages" in modifications:
            # Remove N messages from the start (keep system message)
            n = modifications["remove_messages"]
            system_msg = body["messages"][0] if body["messages"][0]["role"] == "system" else None
            other_msgs = [m for m in body["messages"] if m["role"] != "system"]

            # Remove n messages from the middle of conversation
            if len(other_msgs) > n:
                other_msgs = other_msgs[n:]

            body["messages"] = ([system_msg] if system_msg else []) + other_msgs
            if verbose:
                print(f"  → Removed {n} messages, {len(body['messages'])} remaining")

        if "remove_tool_choice" in modifications and "tool_choice" in body:
            del body["tool_choice"]
            if verbose:
                print("  → Removed tool_choice")

        if "remove_tools" in modifications:
            body["tools"] = []
            if "tool_choice" in body:
                del body["tool_choice"]
            if verbose:
                print("  → Removed all tools")

        if "simplify_messages" in modifications:
            # Keep only last user message
            last_user = [m for m in body["messages"] if m["role"] == "user"][-1]
            system_msg = body["messages"][0] if body["messages"][0]["role"] == "system" else None
            body["messages"] = ([system_msg] if system_msg else []) + [last_user]
            if verbose:
                print(f"  → Simplified to {len(body['messages'])} messages")

    # Get API key
    url = request["url"]
    headers = dict(request["headers"])

    # Remove Content-Length - let httpx calculate it
    headers.pop("content-length", None)
    headers.pop("Content-Length", None)

    if "inference-api.nvidia.com" in url:
        api_key = os.getenv("NVIDIA_INFERENCE_API_KEY") or os.getenv("NVIDIA_INTERNAL_API_KEY")
    elif "integrate.api.nvidia.com" in url or "nvidia" in url:
        api_key = os.getenv("NVIDIA_API_KEY")
    elif "openai.com" in url:
        api_key = os.getenv("OPENAI_API_KEY")
    elif "anthropic.com" in url:
        api_key = os.getenv("ANTHROPIC_API_KEY")
    else:
        api_key = None

    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    # Make request
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.request(
                method=request["method"],
                url=url,
                headers=headers,
                json=body,
            )

            success = response.status_code < 400
            return success, {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.json() if response.content else None,
            }
        except Exception as e:
            return False, {"error": str(e)}


async def test_baseline(request_data: dict):
    """Test a minimal baseline request to ensure setup works."""
    print("\n" + "=" * 70)
    print("BASELINE TEST: Minimal working request")
    print("=" * 70)

    # Create minimal request with same model/endpoint
    minimal = deepcopy(request_data)
    minimal["body"]["messages"] = [{"role": "user", "content": "Say 'hello' in one word"}]
    minimal["body"]["max_tokens"] = 100
    if "tools" in minimal["body"]:
        del minimal["body"]["tools"]
    if "tool_choice" in minimal["body"]:
        del minimal["body"]["tool_choice"]
    if "parallel_tool_calls" in minimal["body"]:
        del minimal["body"]["parallel_tool_calls"]

    print("\nRequest: Simple 'hello' with no tools, max_tokens=100")
    success, response = await test_request(minimal, verbose=False)

    if success:
        print(f"✅ BASELINE WORKS - Status: {response.get('status_code', 'unknown')}")
        content = response.get("body", {})
        if "choices" in content:
            msg = content["choices"][0].get("message", {}).get("content", "")
            print(f"   Response: {msg[:100]}")
        return True
    else:
        print(f"❌ BASELINE FAILED - Status: {response.get('status_code', 'error')}")
        if "body" in response and "error" in response.get("body", {}):
            print(f"   Error: {response['body']['error'].get('message', 'Unknown')}")
        elif "error" in response:
            print(f"   Error: {response['error']}")
        return False


async def divide_and_conquer(request_data: dict):
    """Systematically narrow down what causes the failure."""
    print("\n" + "=" * 70)
    print("DIVIDE & CONQUER: Testing modifications")
    print("=" * 70)

    tests = [
        ("Original (failing)", {}),
        ("Reduce max_tokens to 4096", {"max_tokens": 4096}),
        ("Reduce max_tokens to 2048", {"max_tokens": 2048}),
        ("Remove tool_choice", {"remove_tool_choice": True}),
        ("Remove all tools", {"remove_tools": True}),
        ("Remove first 2 messages", {"remove_messages": 2}),
        ("Remove first 4 messages", {"remove_messages": 4}),
        ("Simplify to last message only", {"simplify_messages": True}),
    ]

    results = []

    for description, modifications in tests:
        print(f"\n{'─' * 70}")
        print(f"TEST: {description}")
        print(f"{'─' * 70}")

        success, response = await test_request(request_data, modifications)

        status = response.get("status_code", "error")
        if success:
            print(f"✅ SUCCESS - Status: {status}")
        else:
            print(f"❌ FAILED - Status: {status}")
            if "body" in response and "error" in response["body"]:
                error_msg = response["body"]["error"].get("message", "Unknown")
                # Truncate long error messages
                if len(error_msg) > 150:
                    error_msg = error_msg[:150] + "..."
                print(f"   Error: {error_msg}")

        results.append((description, success, status))

        # Small delay to avoid rate limits
        await asyncio.sleep(0.5)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for description, success, status in results:
        symbol = "✅" if success else "❌"
        print(f"{symbol} {description:40s} → {status}")

    # Analysis
    print("\n" + "=" * 70)
    print("ANALYSIS")
    print("=" * 70)

    if all(not success for _, success, _ in results):
        print("❌ ALL tests failed - likely an API key or endpoint issue")
    elif results[0][1]:  # Original succeeded
        print("✅ Original request works - not reproducible")
    else:
        # Find first success
        first_success = next((desc for desc, success, _ in results if success), None)
        if first_success:
            print(f"💡 First success: {first_success}")
            print("   → Compare this with failing tests to isolate the issue")
        else:
            print("❌ No modifications made it work")
            print("   → Issue may be with the model itself or a server-side bug")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Debug failing LLM requests")
    parser.add_argument("error_file", type=Path, help="Path to llm_errors.jsonl")
    parser.add_argument("--entry", type=int, default=1, help="Which entry to test (1-indexed)")
    parser.add_argument("--baseline-only", action="store_true", help="Only test baseline")
    parser.add_argument("--max-tokens", type=int, help="Override max_tokens")
    parser.add_argument("--remove-messages", type=int, help="Remove N messages")
    parser.add_argument("--no-tool-choice", action="store_true", help="Remove tool_choice")

    args = parser.parse_args()

    # Load error entry
    with open(args.error_file) as f:
        entries = [json.loads(line) for line in f if line.strip()]

    if args.entry > len(entries):
        print(f"Error: Only {len(entries)} entries in file")
        return

    entry = entries[args.entry - 1]

    print("=" * 70)
    print("LLM REQUEST DEBUGGER")
    print("=" * 70)
    print(f"Model: {entry['model']}")
    print(f"URL: {entry['request']['url']}")
    print(f"Original error: {entry['response']['status_code']}")
    print(f"Timestamp: {entry['timestamp']}")

    # Test baseline
    baseline_works = await test_baseline(entry["request"])

    if not baseline_works:
        print("\n❌ BASELINE FAILED - Fix this first before debugging the actual request")
        return

    if args.baseline_only:
        return

    # Apply custom modifications if specified
    if args.max_tokens or args.remove_messages or args.no_tool_choice:
        print("\n" + "=" * 70)
        print("CUSTOM TEST")
        print("=" * 70)

        modifications = {}
        if args.max_tokens:
            modifications["max_tokens"] = args.max_tokens
        if args.remove_messages:
            modifications["remove_messages"] = args.remove_messages
        if args.no_tool_choice:
            modifications["remove_tool_choice"] = True

        success, response = await test_request(entry["request"], modifications)

        if success:
            print(f"✅ SUCCESS - Status: {response['status_code']}")
        else:
            print(f"❌ FAILED - Status: {response['status_code']}")
    else:
        # Run divide and conquer
        await divide_and_conquer(entry["request"])


if __name__ == "__main__":
    asyncio.run(main())
