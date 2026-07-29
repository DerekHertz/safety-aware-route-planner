"""Deployment wiring: CORS from the environment, and a health check that
actually reflects readiness.

Both exist because the deployed API runs on a different origin from the
front-end and behind a platform health check, neither of which local
development exercises.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import _cors_origins, create_app
from pyref.config import DEFAULT_CONFIG_PATH, Config
from tests.helpers.fixtures import unprotected_left_city


@pytest.fixture()
def toy_pack_env(tmp_path, monkeypatch):
    pack, _ids = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    return pack


class TestCorsOrigins:
    def test_falls_back_to_config(self, monkeypatch):
        monkeypatch.delenv("SR_CORS_ORIGINS", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert _cors_origins(cfg) == list(cfg["api"]["cors_origins"])

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv(
            "SR_CORS_ORIGINS", "https://a.example, https://b.example"
        )
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        # whitespace around the comma is stripped — pasting a list into a
        # dashboard field should not silently produce an origin with a leading
        # space, which would never match.
        assert _cors_origins(cfg) == ["https://a.example", "https://b.example"]

    def test_empty_env_falls_back_rather_than_blocking_everything(self, monkeypatch):
        monkeypatch.setenv("SR_CORS_ORIGINS", "")
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert _cors_origins(cfg) == list(cfg["api"]["cors_origins"])


class TestCorsHeaders:
    def test_configured_origin_is_allowed(self, toy_pack_env, monkeypatch):
        monkeypatch.setenv("SR_CORS_ORIGINS", "https://app.example")
        with TestClient(create_app()) as c:
            r = c.get("/meta", headers={"Origin": "https://app.example"})
            assert r.headers.get("access-control-allow-origin") == "https://app.example"

    def test_unlisted_origin_is_not_allowed(self, toy_pack_env, monkeypatch):
        monkeypatch.setenv("SR_CORS_ORIGINS", "https://app.example")
        with TestClient(create_app()) as c:
            r = c.get("/meta", headers={"Origin": "https://evil.example"})
            assert "access-control-allow-origin" not in r.headers

    def test_regex_matches_generated_preview_hostnames(self, toy_pack_env, monkeypatch):
        """Preview deployments get a hostname per build, so a static list can
        never cover them."""
        monkeypatch.setenv("SR_CORS_ORIGINS", "https://app.example")
        monkeypatch.setenv(
            "SR_CORS_ORIGIN_REGEX", r"^https://myproject-[a-z0-9]+\.example$"
        )
        with TestClient(create_app()) as c:
            ok = c.get("/meta", headers={"Origin": "https://myproject-abc123.example"})
            assert ok.headers.get("access-control-allow-origin") == (
                "https://myproject-abc123.example"
            )
            # The anchoring matters: a lookalike host must not slip through.
            bad = c.get("/meta", headers={"Origin": "https://myproject-abc123.evil.com"})
            assert "access-control-allow-origin" not in bad.headers


class TestHealth:
    def test_reports_ready_state_and_engine(self, toy_pack_env):
        with TestClient(create_app()) as c:
            r = c.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert body["packs_loaded"] == 1
            assert body["num_edges"] > 0
            # Surfaces a silent downgrade to the pure-Python engine, which
            # otherwise only ever shows up as latency.
            assert body["engine"] in {"cpp", "pyref"}

    def test_reports_503_before_the_pack_is_loaded(self, toy_pack_env):
        """Without the TestClient context manager the lifespan never runs, which
        is the same state a container is in between process start and pack load.
        A platform health check must not route traffic there."""
        c = TestClient(create_app())
        r = c.get("/health")
        assert r.status_code == 503
        assert r.json()["status"] == "starting"
