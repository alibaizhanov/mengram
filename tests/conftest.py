"""Shared fixtures.

The receipt ledger lives in the real home directory, and the hook tests run
the real hooks: without this every test run would leave its events in the
developer's own receipt.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_receipt(tmp_path, monkeypatch):
    from local import receipt
    monkeypatch.setattr(receipt, "path", lambda: tmp_path / "receipts.jsonl")
    yield
