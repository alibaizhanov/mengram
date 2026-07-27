"""Secret redaction — fast, no-network unit tests (run in CI on every commit)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.extractor.redact import redact_secrets


def test_openai_key():
    assert "sk-proj-AbCdEf123456789" not in redact_secrets("key sk-proj-AbCdEf123456789 here")


def test_contextual_generic_token():
    out = redact_secrets("my Railway token is 8f3a-secret-token-999")
    assert "8f3a-secret-token-999" not in out and "[REDACTED]" in out


def test_github_pat():
    assert "github_pat_11ABCDEFG0hijklmnop123" not in redact_secrets("github_pat_11ABCDEFG0hijklmnop123")


def test_jwt():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY.abcdef"
    assert jwt not in redact_secrets(f"token {jwt}")


def test_does_not_over_redact_normal_text():
    for clean in ["the deploy runs at 3pm on port 8080",
                  "I use Postgres 16 and Redis 7",
                  "the meeting is at 2:30 with 5 people"]:
        assert redact_secrets(clean) == clean


def test_password_contextual():
    assert "hunter2horse-99" not in redact_secrets("password: hunter2horse-99")


def test_empty_and_none_safe():
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None


def test_idempotent():
    once = redact_secrets("key sk-proj-AbCdEf123456789")
    assert redact_secrets(once) == once
