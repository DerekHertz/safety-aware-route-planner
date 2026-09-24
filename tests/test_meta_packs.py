"""ADR-0014 step 6: `/meta` lists every served pack (decision 6).

`packs` is additive: one `{region, bbox, num_edges}` per served pack, in
`[api] regions` order, the first being the default. The legacy top-level
`region`/`bbox`/`num_edges` keep describing the *default* pack, so a client
that predates `packs` keeps working. Before this step `/meta` read
`registry.only()` and was a 500 on any deployment serving two packs.

The two-pack cases run on the toy metros of `tests/helpers/multi_pack.py`;
the single-pack cases are the null-bbox `SR_PACK_DIR` toy and a one-entry
served list.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pyref.config import Config
from tests.helpers.fixtures import unprotected_left_city
from tests.helpers.multi_pack import A_BBOX, B_BBOX, TWO, build_toy, write_config


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """No pinned pack, no served-list override, no network."""
    monkeypatch.delenv("SR_PACK_DIR", raising=False)
    monkeypatch.delenv("SR_REGIONS", raising=False)
    import api.main
    monkeypatch.setattr(api.main, "ensure_packs", lambda *a, **kw: [])
    return tmp_path, monkeypatch


def _deploy(env, *, build, regions):
    tmp_path, monkeypatch = env
    cfg_path = write_config(tmp_path, presets=TWO, regions=regions)
    monkeypatch.setenv("SR_CONFIG", str(cfg_path))
    cfg = Config.load(cfg_path)
    for name in build:
        build_toy(cfg, name, tmp_path / "packs")
    from api.main import create_app
    return TestClient(create_app())


def test_two_packs_are_listed_in_served_order(env):
    client = _deploy(env, build=["metro_a", "metro_b"],
                     regions=["metro_a", "metro_b"])
    with client:
        resp = client.get("/meta")
        reg = client.app.state.app_state.registry
    assert resp.status_code == 200
    packs = resp.json()["packs"]
    assert packs == [
        {"region": "metro_a", "bbox": A_BBOX,
         "num_edges": reg["metro_a"].pack.num_edges},
        {"region": "metro_b", "bbox": B_BBOX,
         "num_edges": reg["metro_b"].pack.num_edges},
    ]


def test_legacy_fields_describe_the_default_pack(env):
    """The first served pack is the default; an old client reading only the
    top-level fields sees exactly it."""
    client = _deploy(env, build=["metro_a", "metro_b"],
                     regions=["metro_b", "metro_a"])
    with client:
        data = client.get("/meta").json()
    assert [p["region"] for p in data["packs"]] == ["metro_b", "metro_a"]
    default = data["packs"][0]
    assert {k: data[k] for k in ("region", "bbox", "num_edges")} == default
    assert data["region"] == "metro_b"
    assert data["bbox"] == B_BBOX


def test_one_served_pack_lists_itself(env):
    client = _deploy(env, build=["metro_a"], regions=["metro_a"])
    with client:
        data = client.get("/meta").json()
    assert data["region"] == "metro_a"
    assert data["bbox"] == A_BBOX
    assert data["packs"] == [{k: data[k] for k in ("region", "bbox", "num_edges")}]


def test_pinned_null_bbox_toy_is_unchanged_apart_from_packs(tmp_path, monkeypatch):
    """`SR_PACK_DIR` toys carry no bbox: the one pack is listed with a null
    bbox, which the client reads as "no coverage check"."""
    pack, _ = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    from api.main import create_app
    with TestClient(create_app()) as c:
        resp = c.get("/meta")
    assert resp.status_code == 200
    data = resp.json()
    legacy = {"region": "toy", "bbox": None, "num_edges": pack.num_edges}
    assert {k: data[k] for k in legacy} == legacy
    assert data["packs"] == [legacy]
