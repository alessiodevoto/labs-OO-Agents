import inspect

from agent006.runtime.context import ContextApi
from agent006.runtime.events import EventsApi
from agent006.skill import Skill
from agent006.tools.method_writing_lib import MethodWriting


def _check_library_docstring(cls):
    doc = cls.__doc__
    lines = doc.strip().splitlines()
    assert lines[0].strip(), "first line must be the 1-liner"
    assert len(lines[0].strip()) < 200, "1-liner must be concise"
    assert "Examples:" in doc or "Example:" in doc


def test_context_api_has_library_docstring():
    _check_library_docstring(ContextApi)


def test_events_api_has_library_docstring():
    _check_library_docstring(EventsApi)


def test_method_writing_lib_has_library_docstring():
    _check_library_docstring(MethodWriting)


def test_method_writing_lib_is_instantiable():
    lib = MethodWriting()
    assert lib is not None


def test_builtin_libs_are_skills():
    assert issubclass(ContextApi, Skill)
    assert issubclass(EventsApi, Skill)
    assert issubclass(MethodWriting, Skill)


def test_codeact_strategy_instructions_no_longer_has_decomposition():
    from agent006.strategies.codeact import CodeActStrategy

    src = inspect.getsource(CodeActStrategy.strategy_instructions)
    assert "Task decomposition" not in src


def test_context_api_not_in_framework_blocks():
    from agent006.agent import Agent

    assert "context_api" not in Agent._framework_blocks
    assert "events_api" not in Agent._framework_blocks


def test_codeact_block_order_no_api_keys():
    from agent006.strategies.codeact import CodeActStrategy

    strategy = CodeActStrategy.__new__(CodeActStrategy)
    order = strategy.get_block_order() or []
    assert "context_api" not in order
    assert "events_api" not in order
