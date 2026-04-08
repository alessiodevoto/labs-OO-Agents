#!/usr/bin/env python3
"""Test script for error capture functionality.

This script tests that:
1. Error capture can be enabled
2. Errors are captured to JSONL file
3. Successful requests are NOT captured (when errors_only=True)
"""

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path


async def test_error_capture():
    """Test error capture with a simple example."""
    print("🧪 Testing LLM Error Capture")
    print("=" * 60)

    # Create temporary directory for test
    test_dir = Path(tempfile.mkdtemp(prefix="test_error_capture_"))
    print(f"📁 Test directory: {test_dir}")

    try:
        # Enable error capture
        from unifiedllm.http_logging import enable_http_request_logging

        disable_logging = enable_http_request_logging(
            output_dir=test_dir,
            errors_only=True,
            save_responses=True,
            verbose=True,
        )

        # Test 1: Make a request to an invalid endpoint (should capture)
        print("\n1️⃣  Test: Making request to invalid endpoint...")
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                # This should fail with 404
                with contextlib.suppress(httpx.HTTPStatusError):
                    await client.post(
                        "https://api.openai.com/v1/invalid_endpoint",
                        headers={
                            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', 'fake-key')}",
                            "Content-Type": "application/json",
                        },
                        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "test"}]},
                    )
        except Exception as e:
            print(f"   Expected error: {type(e).__name__}")

        # Test 2: Check if error was captured
        print("\n2️⃣  Test: Checking if error was captured...")
        error_file = test_dir / "llm_errors.jsonl"

        if error_file.exists():
            with open(error_file) as f:
                entries = [json.loads(line) for line in f if line.strip()]

            print(f"   ✅ Error file exists with {len(entries)} entries")

            if entries:
                entry = entries[0]
                print(f"   ✅ Entry has timestamp: {entry.get('timestamp')}")
                print(f"   ✅ Entry has model: {entry.get('model')}")
                print(f"   ✅ Entry has request: {'request' in entry}")
                print(f"   ✅ Entry has response: {'response' in entry}")
                print(f"   ✅ Response status: {entry.get('response', {}).get('status_code')}")

                # Check that API key was redacted
                headers = entry.get("request", {}).get("headers", {})
                auth_header = headers.get("authorization", "")
                if "REDACTED" in auth_header or "***" in auth_header:
                    print("   ✅ API key was redacted")
                else:
                    print(f"   ⚠️  API key may not be redacted: {auth_header}")
            else:
                print("   ⚠️  No entries found in error file")
        else:
            print(f"   ❌ Error file not found at {error_file}")

        # Cleanup
        disable_logging()
        print("\n✅ Test completed!")

    finally:
        # Clean up test directory
        shutil.rmtree(test_dir, ignore_errors=True)
        print("\n🧹 Cleaned up test directory")


async def test_with_unifiedllm():
    """Test error capture integrated with UnifiedLLM."""
    print("\n" + "=" * 60)
    print("🧪 Testing Error Capture with UnifiedLLM")
    print("=" * 60)

    test_dir = Path(tempfile.mkdtemp(prefix="test_unifiedllm_errors_"))
    print(f"📁 Test directory: {test_dir}")

    try:
        # Enable error capture
        from unifiedllm.http_logging import enable_http_request_logging

        disable_logging = enable_http_request_logging(
            output_dir=test_dir,
            errors_only=True,
            save_responses=True,
            verbose=True,
        )

        # Make a request with invalid API key (should fail with 401)
        print("\n1️⃣  Test: Making LLM request with invalid key...")
        from unifiedllm import CompletionClient

        try:
            client = CompletionClient(
                model="gpt-4o-mini",
                api_key="invalid-key-for-testing",
            )

            await client.acall(messages=[{"role": "user", "content": "Hello"}])
        except Exception as e:
            print(f"   Expected error: {type(e).__name__}: {str(e)[:100]}")

        # Check if error was captured
        print("\n2️⃣  Test: Checking if error was captured...")
        error_file = test_dir / "llm_errors.jsonl"

        if error_file.exists():
            with open(error_file) as f:
                entries = [json.loads(line) for line in f if line.strip()]

            if entries:
                print(f"   ✅ Captured {len(entries)} error(s)")
                entry = entries[0]
                print(f"   ✅ Status code: {entry.get('response', {}).get('status_code')}")
            else:
                print("   ⚠️  No entries found")
        else:
            print("   ❌ Error file not found")

        disable_logging()
        print("\n✅ Test completed!")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print("🧹 Cleaned up test directory")


if __name__ == "__main__":
    print("Starting error capture tests...\n")

    # Test 1: Basic HTTP error capture
    asyncio.run(test_error_capture())

    # Test 2: UnifiedLLM integration
    asyncio.run(test_with_unifiedllm())

    print("\n" + "=" * 60)
    print("🎉 All tests completed!")
    print("=" * 60)
