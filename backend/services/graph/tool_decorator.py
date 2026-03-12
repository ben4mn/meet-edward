"""
Custom @tool decorator replacing langchain_core.tools.tool.

Creates EdwardTool objects with the same interface consumed by the codebase:
  - .name (str) — from function name
  - .description (str) — from docstring
  - .args_schema (Pydantic model) — auto-generated from function signature
  - .ainvoke(args_dict) — async execution
"""

import asyncio
import inspect
from typing import Any, Callable, Optional, get_type_hints

from pydantic import create_model


class EdwardTool:
    """Lightweight tool object replacing langchain_core.tools.BaseTool."""

    def __init__(self, func: Callable, name: str, description: str, args_schema):
        self.func = func
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self._is_async = inspect.iscoroutinefunction(func)

    async def ainvoke(self, args: dict) -> Any:
        if self._is_async:
            return await self.func(**args)
        return await asyncio.to_thread(self.func, **args)

    def invoke(self, args: dict) -> Any:
        if self._is_async:
            raise RuntimeError(f"Tool '{self.name}' is async; use ainvoke()")
        return self.func(**args)

    def __repr__(self):
        return f"EdwardTool(name={self.name!r})"


def _build_args_schema(func: Callable) -> type:
    """Build a Pydantic model from a function's signature and type hints.

    Handles: required params, Optional[X], defaults (None, int, bool, str),
    List[X], List[Dict], and all combinations found in the codebase.
    """
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    sig = inspect.signature(func)
    fields: dict[str, Any] = {}

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        annotation = hints.get(param_name, str)

        if param.default is inspect.Parameter.empty:
            # Required field — use ... as sentinel
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    model_name = func.__name__.title().replace("_", "") + "Input"
    return create_model(model_name, **fields)


def tool(func: Callable) -> EdwardTool:
    """Drop-in replacement for @langchain_core.tools.tool decorator.

    Usage is identical:
        @tool
        async def my_tool(arg1: str, arg2: int = 5) -> str:
            '''Tool description from docstring.'''
            ...
    """
    name = func.__name__
    description = (func.__doc__ or "").strip()
    args_schema = _build_args_schema(func)

    return EdwardTool(
        func=func,
        name=name,
        description=description,
        args_schema=args_schema,
    )
