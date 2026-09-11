"""A missing folder must not send you off to create a second one.

`mengram local init /somewhere` prints an `export MENGRAM_MEMORY_DIR=...` line,
and every later command needs it. Forget the export and the old message said
"no memory folder at memory — run: mengram local init memory", which creates a
new empty store in the current directory and strands the real one. The user
then sees an empty memory and concludes the product lost their data.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from local import cli as lcli


class _Args:
    memory = None


def _run(monkeypatch, capsys, tmp_path, memory=None, env=None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    if env:
        monkeypatch.setenv("MENGRAM_MEMORY_DIR", env)
    args = _Args()
    args.memory = memory
    with pytest.raises(SystemExit) as e:
        lcli._open(args)
    assert e.value.code == 1
    return capsys.readouterr().err


def test_the_default_path_offers_the_env_var_first(monkeypatch, capsys, tmp_path):
    err = _run(monkeypatch, capsys, tmp_path)
    assert "MENGRAM_MEMORY_DIR" in err
    assert "--memory" in err
    # Creating a new one is still offered, but as the second option.
    assert err.index("MENGRAM_MEMORY_DIR") < err.index("local init")


def test_an_explicit_path_just_says_create_it(monkeypatch, capsys, tmp_path):
    """Nothing to point at: the user named a folder and it is not there."""
    err = _run(monkeypatch, capsys, tmp_path, memory=str(tmp_path / "nope"))
    assert "local init" in err
    assert "MENGRAM_MEMORY_DIR" not in err


def test_an_env_var_pointing_nowhere_also_just_says_create_it(monkeypatch, capsys, tmp_path):
    err = _run(monkeypatch, capsys, tmp_path, env=str(tmp_path / "gone"))
    assert "local init" in err
    assert "export MENGRAM_MEMORY_DIR" not in err


def test_an_existing_folder_opens_without_complaint(monkeypatch, tmp_path):
    (tmp_path / "memory").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MENGRAM_MEMORY_DIR", raising=False)
    args = _Args()
    assert lcli._open(args) is not None
