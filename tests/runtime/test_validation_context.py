from agent006.runtime.code_validator import ValidationContext


def test_validation_context_has_exec_globals():
    ctx = ValidationContext()
    assert ctx.exec_globals == {}


def test_validation_context_accepts_exec_globals():
    globs = {"foo": 42}
    ctx = ValidationContext(exec_globals=globs)
    assert ctx.exec_globals is globs
