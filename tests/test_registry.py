"""Every registered extractor must accept the exact kwargs the dispatcher passes,
otherwise extraction silently raises TypeError per task (the v2 rekrute/talent bug)."""
import inspect

import pytest

from extractors import EXTRACTORS

DISPATCH_KWARGS = {"company_hint": "ACME"}


@pytest.mark.parametrize("kind", sorted(EXTRACTORS))
def test_extractor_signature_accepts_dispatcher_kwargs(kind):
    func = EXTRACTORS[kind]
    sig = inspect.signature(func)
    for name, value in DISPATCH_KWARGS.items():
        assert name in sig.parameters, f"{kind} missing parameter {name}"
    # callable with dispatcher-style args on empty html
    jobs = func("test", "<html><body></body></html>", "https://example.com", "Morocco", **DISPATCH_KWARGS)
    assert jobs == []
