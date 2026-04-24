# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for inherited field visibility in doc().

Verifies that doc() walks the full MRO so parent-class fields appear
in the output alongside child-class fields.
"""

from __future__ import annotations

from typing import Annotated

from nemo_oo_agents.agentdoc import doc
from nemo_oo_agents.agentdoc._structured import extract_type_info
from nemo_oo_agents.agentdoc._visibility import hidden

# ---------------------------------------------------------------------------
# Single-level inheritance
# ---------------------------------------------------------------------------


class Animal:
    """A living creature."""

    name: str = "unnamed"
    legs: int = 4


class Dog(Animal):
    """A domestic dog."""

    breed: str = "mixed"


class TestSingleInheritance:
    def test_doc_shows_parent_fields(self):
        result = doc(Dog)
        assert "name" in result
        assert "legs" in result

    def test_doc_shows_child_fields(self):
        result = doc(Dog)
        assert "breed" in result

    def test_parent_fields_appear_before_child_fields(self):
        result = doc(Dog)
        assert result.index("name") < result.index("breed")
        assert result.index("legs") < result.index("breed")

    def test_extract_type_info_includes_parent_fields(self):
        info = extract_type_info(Dog)
        field_names = [f.name for f in info.fields]
        assert "name" in field_names
        assert "legs" in field_names
        assert "breed" in field_names

    def test_parent_fields_before_child_in_field_list(self):
        info = extract_type_info(Dog)
        field_names = [f.name for f in info.fields]
        assert field_names.index("name") < field_names.index("breed")

    def test_class_still_shown_correctly(self):
        result = doc(Dog)
        assert "class Dog" in result
        assert "A domestic dog." in result


# ---------------------------------------------------------------------------
# Deep inheritance chain
# ---------------------------------------------------------------------------


class Vehicle:
    """A means of transport."""

    speed: float = 0.0
    fuel: str = "gasoline"


class Car(Vehicle):
    """A four-wheeled vehicle."""

    doors: int = 4


class ElectricCar(Car):
    """A battery-powered car."""

    battery_kwh: float = 75.0


class TestDeepInheritance:
    def test_grandparent_fields_shown(self):
        result = doc(ElectricCar)
        assert "speed" in result
        assert "fuel" in result

    def test_parent_fields_shown(self):
        result = doc(ElectricCar)
        assert "doors" in result

    def test_own_fields_shown(self):
        result = doc(ElectricCar)
        assert "battery_kwh" in result

    def test_field_ordering_grandparent_parent_child(self):
        info = extract_type_info(ElectricCar)
        field_names = [f.name for f in info.fields]
        assert field_names.index("speed") < field_names.index("doors")
        assert field_names.index("doors") < field_names.index("battery_kwh")


# ---------------------------------------------------------------------------
# Multiple inheritance (diamond)
# ---------------------------------------------------------------------------


class Flyable:
    """Something that can fly."""

    max_altitude: float = 10_000.0


class Swimmable:
    """Something that can swim."""

    max_depth: float = 50.0


class Duck(Flyable, Swimmable):
    """A duck — flies and swims."""

    quacks: bool = True


class TestMultipleInheritance:
    def test_all_parent_fields_shown(self):
        result = doc(Duck)
        assert "max_altitude" in result
        assert "max_depth" in result
        assert "quacks" in result

    def test_field_ordering_follows_mro(self):
        """MRO of Duck: [Duck, Flyable, Swimmable, object].
        Reversed for collection: [Swimmable, Flyable, Duck].
        So order: max_depth (Swimmable), max_altitude (Flyable), quacks (Duck).
        """
        info = extract_type_info(Duck)
        field_names = [f.name for f in info.fields]
        assert field_names.index("max_altitude") < field_names.index("quacks")
        assert field_names.index("max_depth") < field_names.index("quacks")


class TestDiamondInheritance:
    def test_diamond_field_not_duplicated(self):
        """If A defines a field and B, C both inherit A, D(B, C) should show it once."""

        class A:
            shared: int = 0

        class B(A):
            b_field: str = "b"

        class C(A):
            c_field: str = "c"

        class D(B, C):
            d_field: float = 1.0

        info = extract_type_info(D)
        field_names = [f.name for f in info.fields]
        assert field_names.count("shared") == 1
        assert "b_field" in field_names
        assert "c_field" in field_names
        assert "d_field" in field_names


# ---------------------------------------------------------------------------
# Child overrides parent field
# ---------------------------------------------------------------------------


class TestChildOverridesParent:
    def test_child_redeclares_field_with_new_default(self):
        """Child re-declaring a parent field should use child's default."""

        class Parent:
            x: int = 10

        class Child(Parent):
            x: int = 99  # override

        info = extract_type_info(Child)
        field_names = [f.name for f in info.fields]
        assert field_names.count("x") == 1
        # Should use child's default
        x_field = next(f for f in info.fields if f.name == "x")
        assert x_field.default == 99

    def test_child_unhides_parent_hidden_field(self):
        """Child re-declaring without hidden makes field visible in doc()."""

        class Parent:
            secret: Annotated[str, hidden] = "shh"

        class Child(Parent):
            secret: str = "visible"  # re-declared without hidden

        result = doc(Child)
        assert "secret" in result


# ---------------------------------------------------------------------------
# Hidden parent fields remain hidden in child (if not re-declared)
# ---------------------------------------------------------------------------


class TestHiddenInheritance:
    def test_hidden_parent_field_not_shown_in_child(self):
        class Parent:
            api_key: Annotated[str, hidden] = ""
            label: str = "parent"

        class Child(Parent):
            child_field: int = 0

        result = doc(Child)
        assert "label" in result
        assert "child_field" in result
        assert "api_key" not in result
