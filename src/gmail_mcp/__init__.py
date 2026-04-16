"""Gmail MCP server — local Gmail access via JSON-RPC 2.0 over stdio."""

# ---------------------------------------------------------------------------
# Pydantic / Python 3.14 RC2 compatibility shim
#
# Pydantic 2.13.1 calls the private ``typing._eval_type()`` with a
# ``prefer_fwd_module`` kwarg that existed in an earlier 3.14 build but was
# removed before RC2.  Until Pydantic ships a fix, we strip the unknown kwarg
# so model class creation doesn't crash.
#
# TODO: remove this once Pydantic releases a version that handles 3.14 RC2+.
# ---------------------------------------------------------------------------
import sys
import typing

if sys.version_info >= (3, 14):
    import inspect as _inspect

    _sig = _inspect.signature(typing._eval_type)  # type: ignore[attr-defined]
    if "prefer_fwd_module" not in _sig.parameters:
        _orig_eval_type = typing._eval_type  # type: ignore[attr-defined]

        def _patched_eval_type(*args: object, **kwargs: object) -> object:
            kwargs.pop("prefer_fwd_module", None)
            return _orig_eval_type(*args, **kwargs)  # type: ignore[no-any-return]

        typing._eval_type = _patched_eval_type  # type: ignore[attr-defined]
