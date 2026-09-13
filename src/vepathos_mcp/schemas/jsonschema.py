"""JSON Schema helpers for published tool schemas.

Some MCP clients handle `$ref`/`$defs` poorly, so published schemas are fully inlined. Our models
are not recursive, which makes inlining safe.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

_DROP_KEYS = {"title"}


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = copy.deepcopy(defs[ref.removeprefix("#/$defs/")])
            merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return _resolve(merged, defs)
        return {k: _resolve(v, defs) for k, v in node.items() if k not in _DROP_KEYS and k != "$defs"}
    if isinstance(node, list):
        return [_resolve(item, defs) for item in node]
    return node


def _simplify_optional(node: Any) -> Any:
    """Turn `anyOf: [X, {type: null}]` with a default of null into plain X (optional by omission)."""

    if isinstance(node, dict):
        node = {k: _simplify_optional(v) for k, v in node.items()}
        any_of = node.get("anyOf")
        if (
            isinstance(any_of, list)
            and len(any_of) == 2
            and {"type": "null"} in any_of
            and node.get("default", 0) is None
        ):
            other = next(item for item in any_of if item != {"type": "null"})
            rest = {k: v for k, v in node.items() if k not in {"anyOf", "default"}}
            return {**other, **rest}
        return node
    if isinstance(node, list):
        return [_simplify_optional(item) for item in node]
    return node


def inline_model_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema(mode="validation")
    defs = schema.get("$defs", {})
    resolved = _resolve(schema, defs)
    simplified = _simplify_optional(resolved)
    simplified["type"] = "object"
    return dict(simplified)
