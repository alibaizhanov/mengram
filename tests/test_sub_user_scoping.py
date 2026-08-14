"""Regression tests for sub-user scoping on the write endpoints.

The sub-user is selected by `user_id` in the body, but the read endpoints take
it as a `sub_user_id` query param. The write models declared only `user_id` and
pydantic drops unknown fields by default, so a client that used either of the
other two plausible spellings got a 200 and had every memory written into
`default` instead of its own tenant. Two shipped clients did exactly that:

  * locomo/mengram_benchmark.py posted {"sub_user": "locomo_3"} — ten benchmark
    personas landed in the operator's own default bucket.
  * the Chrome extension posts to /v1/add?sub_user_id=chrome-extension — a
    query param /v1/add never read.

Both now route correctly, and an unrecognized field is a 422 rather than a
silent merge.

Hermetic: imports cloud.sub_user only, so no database or app startup.
"""

import pytest
from pydantic import ValidationError

from cloud.sub_user import SubUserScoped, resolve_sub_user


class _AddLike(SubUserScoped):
    """Stand-in with one required field, like the real AddRequest."""
    text: str


class TestBodySpellings:
    def test_canonical_user_id_still_works(self):
        assert _AddLike(text="x", user_id="bob").user_id == "bob"

    def test_sub_user_id_is_accepted(self):
        assert _AddLike(text="x", sub_user_id="acme-42").user_id == "acme-42"

    def test_sub_user_is_accepted(self):
        assert _AddLike(text="x", sub_user="locomo_3").user_id == "locomo_3"

    def test_absent_falls_back_to_default(self):
        assert _AddLike(text="x").user_id == "default"

    def test_construction_by_field_name_works(self):
        # add_text() builds an AddRequest in-process with user_id=...
        assert _AddLike(text="x", user_id="bob").user_id == "bob"


class TestUnknownFieldsFailLoudly:
    """The actual defect: a dropped field silently merged tenants."""

    def test_misspelled_scope_field_is_rejected(self):
        with pytest.raises(ValidationError):
            _AddLike(text="x", subuser="typo")

    def test_rejection_names_the_offending_field(self):
        with pytest.raises(ValidationError) as exc:
            _AddLike(text="x", tenant_id="acme")
        assert "tenant_id" in str(exc.value)


class TestResolveSubUser:
    def test_explicit_body_value_wins_over_query(self):
        assert resolve_sub_user("bob", "chrome-extension") == "bob"

    def test_query_fills_in_when_body_is_default(self):
        assert resolve_sub_user("default", "chrome-extension") == "chrome-extension"

    def test_query_fills_in_when_body_is_absent(self):
        assert resolve_sub_user(None, "chrome-extension") == "chrome-extension"

    def test_default_when_neither_is_given(self):
        assert resolve_sub_user("default", None) == "default"
        assert resolve_sub_user(None, None) == "default"


class TestShippedClientRegressions:
    def test_locomo_benchmark_payload_isolates_conversations(self):
        # The payload mengram_benchmark.py actually sent.
        a = _AddLike(text="x", sub_user="locomo_3")
        b = _AddLike(text="x", sub_user="locomo_7")
        assert a.user_id != b.user_id
        assert "default" not in (a.user_id, b.user_id)

    def test_chrome_extension_query_param_is_honoured(self):
        body = _AddLike(text="x")  # extension sends no scope in the body
        assert resolve_sub_user(body.user_id, "chrome-extension") == "chrome-extension"
