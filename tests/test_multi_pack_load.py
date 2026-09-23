"""ADR-0014 step 2: a deployment serves the packs `[api] regions` names.

`[api] regions` (overridable by `SR_REGIONS`) is the *served* set; it defaults
to `[region.active]`, the *ingestion* target, so a single-pack deployment is
unchanged. Every served pack is loaded eagerly in `lifespan`, before `/health`
reports ready, and startup refuses a set on which coordinate-to-pack would not
be a function (overlap) or a pack with no timezone.

The packs here are toys built by the same `GraphBuilder` the API tests use,
under `tmp_path`, from a config that adds two presets with disjoint bboxes.
The config reaches the app through the existing `SR_CONFIG` hook, with
`[api] pack_dir` rewritten to `tmp_path`; `SR_PACK_DIR` stays unset, since it
pins one pack and bypasses the served list entirely.

Handlers still read `registry.only()` at this step — routing by coordinates
is step 3 — so nothing here calls `/route` on a two-pack deployment.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api.registry import PackConfigError, served_regions
from pyref.config import DEFAULT_CONFIG_PATH, Config
from tests.helpers.fixtures import unprotected_left_city
from tests.helpers.toy_graphs import GraphBuilder

# Disjoint toy metros, [west, south, east, north], nowhere near a real preset.
A_BBOX = [10.0, 10.0, 10.02, 10.01]
B_BBOX = [20.0, 20.0, 20.02, 20.01]
A_OVERLAPPING_B = [19.99, 19.99, 20.01, 20.005]


def _preset(name: str, bbox: list[float], tz: str | None) -> str:
    lines = [f"[region.presets.{name}]", f"bbox = {bbox!r}"]
    if tz is not None:
        lines.append(f'timezone = "{tz}"')
    return "\n".join(lines) + "\n"


def _write_config(tmp_path: Path, *, presets: dict[str, tuple[list[float], str | None]],
                  regions: list[str] | None = None, active: str | None = None) -> Path:
    """The shipped config, with `pack_dir` -> `tmp_path/packs`, extra presets
    appended, and optionally `[api] regions` / `region.active` set."""
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    pack_root = (tmp_path / "packs").as_posix()
    old_dir = 'pack_dir = "data/packs"'
    assert old_dir in text
    text = text.replace(old_dir, f"pack_dir = '{pack_root}'")
    if regions is not None:
        assert "\n[api]\n" in text
        text = text.replace("\n[api]\n", f"\n[api]\nregions = {regions!r}\n", 1)
    if active is not None:
        old_active = 'active = "berkeley_oakland"'
        assert old_active in text
        text = text.replace(old_active, f'active = "{active}"')
    text += "\n" + "\n".join(_preset(n, b, tz) for n, (b, tz) in presets.items())
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _build_toy(cfg: Config, name: str, root: Path) -> None:
    """A two-node pack whose manifest `region` is `name` and whose bbox is
    stamped from `cfg`'s preset, written to `root/name`."""
    west, south, east, north = cfg.bbox(name)
    b = GraphBuilder(cfg)
    n0 = b.node(south + (north - south) / 3, west + (east - west) / 3)
    n1 = b.node(south + 2 * (north - south) / 3, west + 2 * (east - west) / 3)
    b.edge(n0, n1, length_m=200.0)
    b.build(region=name).write(root / name)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Clean slate: no pinned pack, no served-list override, no network."""
    monkeypatch.delenv("SR_PACK_DIR", raising=False)
    monkeypatch.delenv("SR_REGIONS", raising=False)
    import api.main
    calls: list[list[str]] = []

    def fake_ensure(regions, pack_root, *a, **kw):
        calls.append(list(regions))
        return []
    monkeypatch.setattr(api.main, "ensure_packs", fake_ensure)
    return tmp_path, monkeypatch, calls


def _deploy(env, *, presets, build, **cfg_kw):
    tmp_path, monkeypatch, _ = env
    cfg_path = _write_config(tmp_path, presets=presets, **cfg_kw)
    monkeypatch.setenv("SR_CONFIG", str(cfg_path))
    cfg = Config.load(cfg_path)
    for name in build:
        _build_toy(cfg, name, tmp_path / "packs")
    from api.main import create_app
    return TestClient(create_app())


TWO = {"metro_a": (A_BBOX, "America/Los_Angeles"),
       "metro_b": (B_BBOX, "America/New_York")}


# --- loading N packs --------------------------------------------------------------

def test_two_disjoint_packs_both_load_and_health_counts_them(env):
    client = _deploy(env, presets=TWO, build=["metro_a", "metro_b"],
                     regions=["metro_a", "metro_b"])
    with client:
        body = client.get("/health").json()
        reg = client.app.state.app_state.registry
    assert body["status"] == "ok"
    assert body["packs_loaded"] == 2
    assert body["regions"] == ["metro_a", "metro_b"]
    assert body["engine"] in {"cpp", "pyref"}
    assert reg.names() == ["metro_a", "metro_b"]
    assert reg["metro_a"].bbox == tuple(A_BBOX)
    assert reg["metro_b"].bbox == tuple(B_BBOX)


def test_each_served_pack_carries_its_own_timezone(env):
    client = _deploy(env, presets=TWO, build=["metro_a", "metro_b"],
                     regions=["metro_a", "metro_b"])
    with client:
        reg = client.app.state.app_state.registry
        assert reg["metro_a"].tz == ZoneInfo("America/Los_Angeles")
        assert reg["metro_b"].tz == ZoneInfo("America/New_York")


def test_ensure_packs_receives_the_whole_served_list(env):
    _, _, calls = env
    client = _deploy(env, presets=TWO, build=["metro_a", "metro_b"],
                     regions=["metro_a", "metro_b"])
    with client:
        pass
    assert calls == [["metro_a", "metro_b"]]


def test_overlapping_pair_fails_startup(env):
    presets = {"metro_a": (A_OVERLAPPING_B, "UTC"), "metro_b": (B_BBOX, "UTC")}
    client = _deploy(env, presets=presets, build=["metro_a", "metro_b"],
                     regions=["metro_a", "metro_b"])
    with pytest.raises(PackConfigError, match="overlap"), client:
        pass


def test_a_served_preset_without_timezone_fails_startup(env):
    """Not just the first pack: the second of two is checked too."""
    presets = {"metro_a": (A_BBOX, "UTC"), "metro_b": (B_BBOX, None)}
    client = _deploy(env, presets=presets, build=["metro_a", "metro_b"],
                     regions=["metro_a", "metro_b"])
    with pytest.raises(ValueError, match="metro_b.*timezone"), client:
        pass


def test_an_unserved_preset_without_timezone_is_fine(env):
    presets = {"metro_a": (A_BBOX, "UTC"), "metro_b": (B_BBOX, None)}
    client = _deploy(env, presets=presets, build=["metro_a"], regions=["metro_a"])
    with client:
        assert client.get("/health").json()["regions"] == ["metro_a"]


def test_directory_whose_manifest_disagrees_fails_startup(env):
    tmp_path, _, _ = env
    client = _deploy(env, presets=TWO, build=["metro_a"], regions=["metro_a", "metro_b"])
    # metro_b's directory holds metro_a's manifest
    shutil.copytree(tmp_path / "packs" / "metro_a", tmp_path / "packs" / "metro_b")
    with pytest.raises(PackConfigError, match="'metro_b'.*'metro_a'"), client:
        pass


# --- which packs are served ---------------------------------------------------------

def test_default_served_set_is_region_active(env):
    client = _deploy(env, presets=TWO, build=["metro_b"], active="metro_b")
    with client:
        body = client.get("/health").json()
    assert body["packs_loaded"] == 1
    assert body["regions"] == ["metro_b"]
    _, _, calls = env
    assert calls == [["metro_b"]]


def test_single_served_pack_is_the_sole_entry_with_its_zone(env):
    """One served pack is today's deployment: the handlers' `only()` finds it,
    and departures are resolved in that pack's zone."""
    client = _deploy(env, presets=TWO, build=["metro_a"], regions=["metro_a"])
    with client:
        state = client.app.state.app_state
        assert state.registry.only().tz == ZoneInfo("America/Los_Angeles")
        assert state.pack is state.registry["metro_a"].pack


def test_sr_regions_overrides_the_config_list(env):
    _, monkeypatch, calls = env
    monkeypatch.setenv("SR_REGIONS", " metro_b , ,")
    client = _deploy(env, presets=TWO, build=["metro_b"],
                     regions=["metro_a", "metro_b"])
    with client:
        assert client.get("/health").json()["regions"] == ["metro_b"]
    assert calls == [["metro_b"]]


def test_sr_pack_dir_pins_one_pack_and_bypasses_the_served_list(env):
    tmp_path, monkeypatch, calls = env
    pack, _ = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    monkeypatch.setenv("SR_REGIONS", "metro_a,metro_b")
    client = _deploy(env, presets=TWO, build=[])
    with client:
        body = client.get("/health").json()
    assert body["packs_loaded"] == 1
    assert body["regions"] == ["toy"]
    assert calls == []          # no network, no fetch


# --- the pure parser ---------------------------------------------------------------

class TestServedRegions:
    @staticmethod
    def _cfg(api_regions=None, active="metro_a") -> Config:
        base = Config.load(DEFAULT_CONFIG_PATH)
        api = dict(base.raw["api"])
        api.pop("regions", None)
        if api_regions is not None:
            api["regions"] = api_regions
        raw = {**base.raw, "api": api,
               "region": {**base.raw["region"], "active": active}}
        return Config(raw=raw, source_path=base.source_path)

    def test_absent_defaults_to_region_active(self):
        assert served_regions(self._cfg(), env={}) == ["metro_a"]

    def test_config_list_is_used_in_order(self):
        cfg = self._cfg(["metro_b", "metro_a"])
        assert served_regions(cfg, env={}) == ["metro_b", "metro_a"]

    def test_env_overrides_and_is_parsed_like_cors_origins(self):
        cfg = self._cfg(["metro_a"])
        env = {"SR_REGIONS": " metro_b,, metro_c ,"}
        assert served_regions(cfg, env=env) == ["metro_b", "metro_c"]

    def test_empty_env_falls_back_to_config(self):
        cfg = self._cfg(["metro_b"])
        assert served_regions(cfg, env={"SR_REGIONS": ""}) == ["metro_b"]

    def test_explicitly_empty_config_list_is_refused(self):
        with pytest.raises(PackConfigError, match="regions"):
            served_regions(self._cfg([]), env={})

    def test_shipped_config_serves_region_active(self):
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert served_regions(cfg, env={}) == [cfg.region_name]
