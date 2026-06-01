# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for package version reporting."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from unittest.mock import patch

import nemo_oo_agents
import nemo_oo_agents.agentdoc as agentdoc
import nemo_oo_agents.tracing as tracing
from nemo_oo_agents import _version
from nemo_oo_agents.tracing._metadata import get_environment_metadata


def test_package_version_matches_installed_metadata():
    try:
        installed_version = metadata_version("nemo_oo_agents")
    except PackageNotFoundError:
        installed_version = "0.0.0+unknown"

    assert nemo_oo_agents.__version__ == installed_version


def test_get_version_falls_back_when_metadata_is_missing():
    with patch("nemo_oo_agents._version._metadata_version", side_effect=PackageNotFoundError):
        assert _version.get_version() == "0.0.0+unknown"


def test_subpackages_reexport_package_version():
    assert agentdoc.__version__ == nemo_oo_agents.__version__
    assert tracing.__version__ == nemo_oo_agents.__version__


def test_tracing_metadata_uses_package_version():
    metadata = get_environment_metadata()

    assert metadata["nemo_oo_agents.version"] == nemo_oo_agents.__version__


def test_instrumentor_registers_tracer_with_package_version():
    class RecordingTracerProvider:
        def __init__(self):
            self.get_tracer_args = None

        def get_tracer(self, *args):
            self.get_tracer_args = args
            return object()

    tracer_provider = RecordingTracerProvider()
    instrumentor = tracing.NemoOOAgentsInstrumentor()

    with patch("nemo_oo_agents.runtime.hooks.set_hooks"):
        instrumentor.instrument(tracer_provider=tracer_provider)

    assert tracer_provider.get_tracer_args == ("nemo_oo_agents.tracing", nemo_oo_agents.__version__)
