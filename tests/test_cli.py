"""Unit tests for cli.py helper functions used by Claude Code hooks."""

import json
import sys

import pytest

import cli


def test_load_cloud_base_url_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MENGRAM_URL", "http://192.168.2.99:8420/")
    monkeypatch.setattr(cli, "DEFAULT_HOME", tmp_path / ".mengram")
    assert cli._load_cloud_base_url() == "http://192.168.2.99:8420"


def test_load_cloud_base_url_from_config(monkeypatch, tmp_path):
    monkeypatch.delenv("MENGRAM_URL", raising=False)
    home = tmp_path / ".mengram"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({
        "api_key": "om-test",
        "base_url": "http://192.168.2.99:8420/",
    }))
    monkeypatch.setattr(cli, "DEFAULT_HOME", home)
    assert cli._load_cloud_base_url() == "http://192.168.2.99:8420"


def test_load_cloud_base_url_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MENGRAM_URL", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_HOME", tmp_path / ".mengram")
    assert cli._load_cloud_base_url() == "https://mengram.io"
