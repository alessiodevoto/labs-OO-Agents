# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for storage/serialization.py — serialize() / deserialize() dispatch.

Covers every dispatch path, roundtrips, lenient restoration, and allowlist
rejection as specified in the Phase 1 design.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from pydantic import BaseModel

from nemo_oo_agents.errors.storage import DeserializationError, SerializationError
from nemo_oo_agents.storage.markers import snapshotable
from nemo_oo_agents.storage.serialization import SKIP, deserialize, serialize

# ---------------------------------------------------------------------------
# Fixture types
# ---------------------------------------------------------------------------


class MyModel(BaseModel):
    name: str
    value: int = 0


class NestedModel(BaseModel):
    inner: MyModel
    tags: list[str] = []


@dataclasses.dataclass
class Point:
    x: float
    y: float


@dataclasses.dataclass
class Line:
    start: Point
    end: Point


@snapshotable
class Config:
    def __init__(self, host: str, port: int = 8080):
        self.host = host
        self.port = port


@snapshotable
class Nested:
    """A snapshotable class that contains another snapshotable."""

    def __init__(self, config: Config, label: str = "default"):
        self.config = config
        self.label = label


class _NoSnapshotThing:
    __nosnapshot__ = True


class UnsupportedThing:
    """A plain class with no serialization support."""

    pass


@dataclasses.dataclass
class PointWithDefault:
    a: int
    b: int = 99


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class TestPrimitives:
    """serialize() returns JSON primitives as-is; deserialize() roundtrips."""

    @pytest.mark.parametrize(
        "value",
        [None, True, False, 0, 42, -1, 3.14, "", "hello"],
        ids=lambda v: repr(v),
    )
    def test_primitive_roundtrip(self, value: Any):
        blob, allowlist = serialize(value)
        assert blob == value
        assert allowlist == set()
        assert deserialize(blob, allowlist) == value

    def test_int_zero_is_not_skipped(self):
        """Ensure 0 isn't confused with a falsy sentinel."""
        blob, _ = serialize(0)
        assert blob == 0


# ---------------------------------------------------------------------------
# Collections (list, dict, tuple)
# ---------------------------------------------------------------------------


class TestCollections:
    def test_list_of_primitives(self):
        blob, al = serialize([1, "two", None])
        assert blob == [1, "two", None]
        assert al == set()
        assert deserialize(blob, al) == [1, "two", None]

    def test_dict_of_primitives(self):
        blob, al = serialize({"a": 1, "b": True})
        assert blob == {"a": 1, "b": True}
        assert al == set()
        assert deserialize(blob, al) == {"a": 1, "b": True}

    def test_tuple_roundtrips_as_list(self):
        """Tuples become lists after JSON roundtrip — that's expected."""
        blob, al = serialize((1, 2, 3))
        assert blob == [1, 2, 3]
        assert deserialize(blob, al) == [1, 2, 3]

    def test_nested_collection(self):
        value = {"items": [1, {"nested": True}], "count": 2}
        blob, al = serialize(value)
        assert deserialize(blob, al) == value


# ---------------------------------------------------------------------------
# nosnapshot
# ---------------------------------------------------------------------------


class TestNoSnapshot:
    def test_nosnapshot_value_returns_SKIP(self):
        thing = _NoSnapshotThing()
        blob, al = serialize(thing)
        assert blob is SKIP
        assert al == set()

    def test_SKIP_sentinel_identity(self):
        """SKIP is a unique sentinel — not None, not False."""
        assert SKIP is not None
        assert SKIP is not False
        assert repr(SKIP) == "SKIP"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TestPydantic:
    def test_simple_model_roundtrip(self):
        m = MyModel(name="test", value=42)
        blob, al = serialize(m)
        assert blob["__type__"] == "pydantic"
        assert blob["__class__"] == f"{MyModel.__module__}.MyModel"
        assert blob["data"] == {"name": "test", "value": 42}
        restored = deserialize(blob, al)
        assert isinstance(restored, MyModel)
        assert restored.name == "test"
        assert restored.value == 42

    def test_nested_pydantic_roundtrip(self):
        m = NestedModel(inner=MyModel(name="inner", value=1), tags=["a", "b"])
        blob, al = serialize(m)
        restored = deserialize(blob, al)
        assert isinstance(restored, NestedModel)
        assert isinstance(restored.inner, MyModel)
        assert restored.inner.name == "inner"
        assert restored.tags == ["a", "b"]

    def test_pydantic_in_list(self):
        items = [MyModel(name="a", value=1), MyModel(name="b", value=2)]
        blob, al = serialize(items)
        restored = deserialize(blob, al)
        assert len(restored) == 2
        assert all(isinstance(r, MyModel) for r in restored)

    def test_pydantic_schema_drift_tolerant(self):
        """Pydantic model_validate handles extra/missing fields gracefully."""
        blob = {
            "__type__": "pydantic",
            "__class__": f"{MyModel.__module__}.MyModel",
            "data": {"name": "old", "value": 5, "extra_field": "ignored"},
        }
        fqn = f"{MyModel.__module__}.MyModel"
        restored = deserialize(blob, {fqn})
        assert isinstance(restored, MyModel)
        assert restored.name == "old"
        assert restored.value == 5


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestDataclass:
    def test_simple_dataclass_roundtrip(self):
        p = Point(x=1.0, y=2.0)
        blob, al = serialize(p)
        assert blob["__type__"] == "dataclass"
        assert blob["__class__"] == f"{Point.__module__}.Point"
        assert blob["data"] == {"x": 1.0, "y": 2.0}
        restored = deserialize(blob, al)
        assert isinstance(restored, Point)
        assert restored.x == 1.0
        assert restored.y == 2.0

    def test_nested_dataclass_roundtrip(self):
        line = Line(start=Point(0, 0), end=Point(1, 1))
        blob, al = serialize(line)
        restored = deserialize(blob, al)
        assert isinstance(restored, Line)
        assert isinstance(restored.start, Point)
        assert restored.start.x == 0
        assert restored.end.y == 1

    def test_dataclass_lenient_extra_keys(self):
        """Deserialization ignores extra keys not accepted by __init__."""
        blob = {
            "__type__": "dataclass",
            "__class__": f"{Point.__module__}.Point",
            "data": {"x": 1.0, "y": 2.0, "z": 3.0},
        }
        fqn = f"{Point.__module__}.Point"
        restored = deserialize(blob, {fqn})
        assert isinstance(restored, Point)
        assert restored.x == 1.0
        assert restored.y == 2.0

    def test_dataclass_lenient_missing_keys_use_defaults(self):
        """Deserialization lets defaults fill missing keys."""
        blob = {
            "__type__": "dataclass",
            "__class__": f"{PointWithDefault.__module__}.PointWithDefault",
            "data": {"a": 1},
        }
        fqn = f"{PointWithDefault.__module__}.PointWithDefault"
        restored = deserialize(blob, {fqn})
        assert restored.a == 1
        assert restored.b == 99


# ---------------------------------------------------------------------------
# @snapshotable classes
# ---------------------------------------------------------------------------


class TestSnapshotable:
    def test_simple_roundtrip(self):
        cfg = Config(host="localhost", port=9090)
        blob, al = serialize(cfg)
        assert blob["__type__"] == "dict_class"
        assert blob["__class__"] == f"{Config.__module__}.Config"
        assert blob["data"] == {"host": "localhost", "port": 9090}
        restored = deserialize(blob, al)
        assert isinstance(restored, Config)
        assert restored.host == "localhost"
        assert restored.port == 9090

    def test_nested_snapshotable_roundtrip(self):
        nested = Nested(config=Config("example.com", 443), label="prod")
        blob, al = serialize(nested)
        restored = deserialize(blob, al)
        assert isinstance(restored, Nested)
        assert isinstance(restored.config, Config)
        assert restored.config.host == "example.com"
        assert restored.config.port == 443
        assert restored.label == "prod"

    def test_snapshotable_lenient_extra_keys(self):
        """Deserialization ignores extra keys for @snapshotable."""
        blob = {
            "__type__": "dict_class",
            "__class__": f"{Config.__module__}.Config",
            "data": {"host": "localhost", "port": 80, "extra": "ignored"},
        }
        fqn = f"{Config.__module__}.Config"
        restored = deserialize(blob, {fqn})
        assert isinstance(restored, Config)
        assert restored.host == "localhost"
        assert restored.port == 80

    def test_snapshotable_mixed_in_collection(self):
        """@snapshotable inside a list/dict."""
        data = {"configs": [Config("a", 1), Config("b", 2)]}
        blob, al = serialize(data)
        restored = deserialize(blob, al)
        assert len(restored["configs"]) == 2
        assert all(isinstance(c, Config) for c in restored["configs"])


# ---------------------------------------------------------------------------
# Allowlist security
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_deserialization_rejects_unknown_class(self):
        blob = {
            "__type__": "pydantic",
            "__class__": "some.unknown.Module.ClassName",
            "data": {},
        }
        with pytest.raises(DeserializationError, match="not in the allowlist"):
            deserialize(blob, set())

    def test_allowlist_populated_during_serialize(self):
        m = MyModel(name="x", value=1)
        _, al = serialize(m)
        assert f"{MyModel.__module__}.MyModel" in al

    def test_allowlist_contains_all_types(self):
        """serialize() collects FQNs from Pydantic, dataclass, and @snapshotable."""
        data = {
            "model": MyModel(name="a", value=1),
            "point": Point(x=0, y=0),
            "config": Config("h", 80),
        }
        _, al = serialize(data)
        assert f"{MyModel.__module__}.MyModel" in al
        assert f"{Point.__module__}.Point" in al
        assert f"{Config.__module__}.Config" in al

    def test_nested_types_in_allowlist(self):
        """Nested enveloped types are all captured in the allowlist."""
        line = Line(start=Point(0, 0), end=Point(1, 1))
        _, al = serialize(line)
        assert f"{Line.__module__}.Line" in al
        assert f"{Point.__module__}.Point" in al


# ---------------------------------------------------------------------------
# Error: unknown type
# ---------------------------------------------------------------------------


class TestUnsupportedType:
    def test_serialize_raises_for_unknown_type(self):
        with pytest.raises(SerializationError, match="@snapshotable"):
            serialize(UnsupportedThing())

    def test_error_message_mentions_options(self):
        """Error message tells the developer their options."""
        with pytest.raises(SerializationError) as exc_info:
            serialize(UnsupportedThing())
        msg = str(exc_info.value)
        assert "nosnapshot" in msg
        assert "Pydantic" in msg or "pydantic" in msg.lower()

    def test_unsupported_nested_in_list(self):
        with pytest.raises(SerializationError):
            serialize([1, UnsupportedThing()])

    def test_unsupported_nested_in_dict(self):
        with pytest.raises(SerializationError):
            serialize({"key": UnsupportedThing()})


# ---------------------------------------------------------------------------
# Mixed types in a single structure
# ---------------------------------------------------------------------------


class TestMixed:
    def test_dict_with_mixed_values(self):
        data = {
            "name": "test",
            "count": 42,
            "model": MyModel(name="m", value=1),
            "point": Point(x=1, y=2),
            "config": Config("h", 80),
            "items": [1, MyModel(name="i", value=2)],
        }
        blob, al = serialize(data)
        restored = deserialize(blob, al)

        assert restored["name"] == "test"
        assert restored["count"] == 42
        assert isinstance(restored["model"], MyModel)
        assert isinstance(restored["point"], Point)
        assert isinstance(restored["config"], Config)
        assert isinstance(restored["items"][1], MyModel)

    def test_attributes_dict_roundtrip(self):
        """Simulates what snapshot.py does — serialize an attributes dict."""
        attrs = {
            "score": 95,
            "history": ["step1", "step2"],
            "result": MyModel(name="final", value=100),
        }
        blob, al = serialize(attrs)
        restored = deserialize(blob, al)
        assert restored["score"] == 95
        assert restored["history"] == ["step1", "step2"]
        assert isinstance(restored["result"], MyModel)
        assert restored["result"].value == 100


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_list(self):
        blob, al = serialize([])
        assert blob == []
        assert deserialize(blob, al) == []

    def test_empty_dict(self):
        blob, al = serialize({})
        assert blob == {}
        assert deserialize(blob, al) == {}

    def test_deeply_nested(self):
        value = {"a": [{"b": [1, 2, {"c": True}]}]}
        blob, al = serialize(value)
        assert deserialize(blob, al) == value

    def test_dict_with_type_key_not_envelope(self):
        """A dict with '__type__' key but wrong format is NOT treated as envelope."""
        value = {"__type__": "not_a_real_type", "data": 42}
        blob, al = serialize(value)
        restored = deserialize(blob, al)
        assert restored == value

    def test_serialize_deserialize_idempotent(self):
        """serialize(deserialize(blob)) produces the same blob."""
        m = MyModel(name="test", value=1)
        blob1, al1 = serialize(m)
        restored = deserialize(blob1, al1)
        blob2, al2 = serialize(restored)
        assert blob1 == blob2
