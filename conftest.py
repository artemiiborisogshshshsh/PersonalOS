"""Conftest to support async test functions without external plugins.
It wraps coroutine test functions to run them synchronously via asyncio.run.
"""
import inspect
import asyncio
import pytest

def pytest_collection_modifyitems(config, items):
    for item in items:
        # Let pytest-asyncio own explicitly marked coroutine tests. The local
        # wrapper is only a fallback for this repository's unmarked async tests.
        if "asyncio" in item.keywords:
            continue
        # Detect coroutine test functions
        try:
            func = item.function
        except Exception:
            # fallback to obj attribute
            func = getattr(item, 'obj', None)
        if func and inspect.iscoroutinefunction(func):
            original = func
            def sync_wrapper(*args, **kwargs):
                return asyncio.run(original(*args, **kwargs))
            sync_wrapper.__name__ = original.__name__
            sync_wrapper.__doc__ = original.__doc__
            # Replace the underlying object
            if hasattr(item, 'obj'):
                item.obj = sync_wrapper
            else:
                # Fallback: set a private attribute used by pytest
                setattr(item, '_obj', sync_wrapper)
