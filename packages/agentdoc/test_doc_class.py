# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test that doc() works correctly for classes."""

from agentdoc import doc


class TestClass:
    """A test class with methods."""

    def __init__(self):
        self.value = 42

    async def validate(self, values: list[float]) -> dict:
        """Validate the values against common rules."""
        return {"valid": True}

    def process(self, item: str) -> str:
        """Process an item."""
        return item.upper()


# Test doc() on the class itself
result = doc(TestClass)

print("=" * 80)
print("doc(TestClass) output:")
print("=" * 80)
print(result)
print("=" * 80)

# Verify it shows the class name, not "type"
if "# TestClass" in result:
    print("\n✅ SUCCESS: Shows class name correctly!")
else:
    print("\n❌ FAILED: Should show '# TestClass', not '# type'")

# Verify it shows methods
if "async def validate" in result:
    print("✅ SUCCESS: Shows async methods correctly!")
else:
    print("❌ FAILED: Should show validate method")

if "def process" in result:
    print("✅ SUCCESS: Shows regular methods correctly!")
else:
    print("❌ FAILED: Should show process method")
