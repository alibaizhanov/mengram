"""Regression tests for the OAuth redirect policy.

The authorization code exchanges for a full-access API key, so any host that
can receive one can take over an account: send the victim a link to the real
/oauth/authorize carrying an attacker redirect_uri, let them log in on
mengram.io, collect the code. PKCE is no defence — the malicious client picks
its own challenge — and client registration is open, so a per-client list would
be attacker-supplied. The allowlist has to be ours.
"""

import pytest

from cloud.oauth_policy import redirect_uri_error


@pytest.fixture
def allowlist(monkeypatch):
    def _set(value):
        monkeypatch.setenv("OAUTH_REDIRECT_ALLOWLIST", value)
    return _set


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.delenv("OAUTH_REDIRECT_ALLOWLIST", raising=False)


class TestSchemeRules:
    """Enforced whether or not an allowlist is configured."""

    def test_https_is_accepted(self):
        assert redirect_uri_error("https://claude.ai/api/mcp/auth_callback") is None

    def test_plain_http_is_rejected_off_loopback(self):
        assert redirect_uri_error("http://evil.com/cb") == "redirect_uri must use HTTPS"

    def test_loopback_may_use_http_for_local_clients(self):
        assert redirect_uri_error("http://localhost:8931/cb") is None
        assert redirect_uri_error("http://127.0.0.1:8931/cb") is None

    def test_non_web_schemes_are_rejected(self):
        assert redirect_uri_error("javascript:alert(1)") == "Invalid redirect_uri scheme"
        assert redirect_uri_error("file:///etc/passwd") == "Invalid redirect_uri scheme"

    def test_empty_redirect_uri_is_not_an_error(self):
        """Absent redirect_uri means there is nowhere to leak a code to."""
        assert redirect_uri_error("") is None


class TestAllowlistEnforcement:
    def test_unlisted_host_is_rejected(self, allowlist):
        allowlist("claude.ai,claude.com")

        assert redirect_uri_error("https://attacker.com/cb") == "redirect_uri is not allowed"

    def test_listed_host_passes(self, allowlist):
        allowlist("claude.ai,claude.com")

        assert redirect_uri_error("https://claude.ai/api/mcp/auth_callback") is None
        assert redirect_uri_error("https://claude.com/api/mcp/auth_callback") is None

    def test_subdomains_of_a_listed_host_pass(self, allowlist):
        allowlist("claude.ai")

        assert redirect_uri_error("https://api.claude.ai/cb") is None

    def test_suffix_lookalike_does_not_pass(self, allowlist):
        """notclaude.ai must not slip through an endswith check."""
        allowlist("claude.ai")

        assert redirect_uri_error("https://notclaude.ai/cb") == "redirect_uri is not allowed"

    def test_host_match_is_case_insensitive(self, allowlist):
        allowlist("Claude.AI")

        assert redirect_uri_error("https://CLAUDE.ai/cb") is None

    def test_loopback_still_allowed_under_an_allowlist(self, allowlist):
        """Local MCP clients bind random loopback ports; never lock them out."""
        allowlist("claude.ai")

        assert redirect_uri_error("http://localhost:9999/cb") is None

    def test_whitespace_and_blank_entries_are_ignored(self, allowlist):
        allowlist(" claude.ai , , ")

        assert redirect_uri_error("https://claude.ai/cb") is None
        assert redirect_uri_error("https://attacker.com/cb") == "redirect_uri is not allowed"

    def test_allowlist_is_read_per_call(self, allowlist):
        """Set the env var and the next request is already governed by it."""
        assert redirect_uri_error("https://attacker.com/cb") is None  # allowlist off

        allowlist("claude.ai")
        assert redirect_uri_error("https://attacker.com/cb") == "redirect_uri is not allowed"
