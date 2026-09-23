"""ADR-0014 step 1: the pack registry, request-to-pack selection, and the
startup validation that keeps coordinate-to-pack a function.

Points are **(lat, lon)**, the order `/route` and `/reroute` already take
them in (`LatLon`). Bboxes are the manifest's **[west, south, east, north]**,
i.e. (lon_min, lat_min, lon_max, lat_max). The two orders differ, which is
exactly why the tests below use asymmetric toy boxes: a swapped axis fails.
"""
from __future__ import annotations

import pytest

from api.registry import (
    DestinationOutside,
    DifferentPacks,
    OriginOutside,
    PackConfigError,
    PackRegistry,
    bbox_contains,
    load_named_pack,
    load_pinned_pack,
    pack_for,
    validate_coverage,
    validate_pack_name,
)
from pyref.config import Config
from tests.helpers.fixtures import unprotected_left_city
from tests.helpers.toy_graphs import GraphBuilder

# Two disjoint toy boxes, deliberately wider in lon than in lat so a (lon, lat)
# point would land somewhere else entirely.
A = (0.0, 10.0, 4.0, 12.0)    # west, south, east, north
B = (5.0, 10.0, 9.0, 12.0)
TWO = [("a", A), ("b", B)]


# ------------------------------------------------------------ containment
class TestBboxContains:
    def test_interior_point(self):
        assert bbox_contains(A, (11.0, 2.0))

    def test_point_is_lat_lon_not_lon_lat(self):
        # (lat=2, lon=11) is outside A; the swapped reading (lon=2, lat=11)
        # would be inside. Pins the axis order.
        assert not bbox_contains(A, (2.0, 11.0))

    @pytest.mark.parametrize("pt", [
        (10.0, 2.0),   # on the south edge
        (12.0, 2.0),   # on the north edge
        (11.0, 0.0),   # on the west edge
        (11.0, 4.0),   # on the east edge
        (10.0, 0.0),   # south-west corner
        (12.0, 4.0),   # north-east corner
    ])
    def test_boundary_is_inside_closed_intervals(self, pt):
        assert bbox_contains(A, pt)

    @pytest.mark.parametrize("pt", [
        (9.999999, 2.0), (12.000001, 2.0), (11.0, -0.000001), (11.0, 4.000001),
    ])
    def test_just_outside_is_outside(self, pt):
        assert not bbox_contains(A, pt)

    def test_null_bbox_contains_everything(self):
        # Only legal for a sole served pack (see validate_coverage): there is
        # nothing to choose between, so snapping decides as it always has.
        assert bbox_contains(None, (-89.0, 179.0))


# ------------------------------------------------------------- pack_for
class TestPackFor:
    def test_both_in_same_pack(self):
        assert pack_for(TWO, (11.0, 1.0), (11.5, 3.0)) == "a"
        assert pack_for(TWO, (11.0, 6.0), (10.5, 8.0)) == "b"

    def test_origin_outside_everything(self):
        err = pack_for(TWO, (50.0, 1.0), (11.0, 1.0))
        assert isinstance(err, OriginOutside)
        assert err.detail == "origin is outside every served region"

    def test_destination_outside_everything(self):
        err = pack_for(TWO, (11.0, 1.0), (11.0, 4.5))   # lon 4.5: the gap
        assert isinstance(err, DestinationOutside)
        assert err.detail == "destination is outside every served region"

    def test_both_outside_reports_origin(self):
        assert isinstance(pack_for(TWO, (0.0, 0.0), (0.0, 0.0)), OriginOutside)

    def test_different_packs_names_both_in_order(self):
        err = pack_for(TWO, (11.0, 8.0), (11.0, 1.0))
        assert err == DifferentPacks(origin_pack="b", destination_pack="a")
        assert err.detail == ("origin and destination are in different regions "
                              "(b, a); routing across regions is not supported")

    def test_boundary_point_selects_the_pack(self):
        assert pack_for(TWO, (10.0, 0.0), (12.0, 4.0)) == "a"

    def test_sole_null_bbox_pack_takes_everything(self):
        assert pack_for([("toy", None)], (1.0, 2.0), (-3.0, -4.0)) == "toy"

    def test_no_packs_is_origin_outside(self):
        assert isinstance(pack_for([], (1.0, 2.0), (1.0, 2.0)), OriginOutside)


# ------------------------------------------------------ startup validation
class TestValidateCoverage:
    def test_disjoint_is_fine(self):
        validate_coverage(TWO)

    def test_single_pack_is_fine(self):
        validate_coverage([("a", A)])

    def test_overlap_is_refused_and_names_both(self):
        with pytest.raises(PackConfigError, match=r"'a'.*'c'.*overlap"):
            validate_coverage([("a", A), ("c", (3.0, 11.0, 6.0, 13.0))])

    def test_containment_is_overlap(self):
        # berkeley_small inside berkeley_oakland is the motivating case
        with pytest.raises(PackConfigError, match="overlap"):
            validate_coverage([("big", (0.0, 0.0, 10.0, 10.0)),
                               ("small", (2.0, 2.0, 3.0, 3.0))])

    def test_touching_edges_count_as_overlap(self):
        # Containment is closed, so a shared edge is in both boxes and would
        # make pack_for ambiguous there.
        with pytest.raises(PackConfigError, match="overlap"):
            validate_coverage([("a", A), ("t", (4.0, 10.0, 8.0, 12.0))])

    def test_touching_corner_counts_as_overlap(self):
        with pytest.raises(PackConfigError, match="overlap"):
            validate_coverage([("a", A), ("t", (4.0, 12.0, 8.0, 14.0))])

    def test_null_bbox_alone_is_fine(self):
        validate_coverage([("toy", None)])

    def test_null_bbox_with_others_is_refused(self):
        with pytest.raises(PackConfigError, match="'toy'.*no bbox"):
            validate_coverage([("a", A), ("toy", None)])

    def test_duplicate_name_is_refused(self):
        with pytest.raises(PackConfigError, match="more than once"):
            validate_coverage([("a", A), ("a", B)])

    @pytest.mark.parametrize("bad", [
        (4.0, 10.0, 0.0, 12.0),   # west > east
        (0.0, 12.0, 4.0, 10.0),   # south > north
        (0.0, 10.0, 4.0),         # wrong arity
    ])
    def test_malformed_bbox_is_refused(self, bad):
        with pytest.raises(PackConfigError, match="bbox"):
            validate_coverage([("a", bad)])


class TestValidatePackName:
    def test_match(self):
        validate_pack_name("berkeley_small", {"region": "berkeley_small"})

    def test_mismatch_is_fatal(self):
        with pytest.raises(PackConfigError, match="'berkeley_small'.*'toy'"):
            validate_pack_name("berkeley_small", {"region": "toy"})

    def test_missing_region_is_fatal(self):
        with pytest.raises(PackConfigError, match="region"):
            validate_pack_name("berkeley_small", {})


# ------------------------------------------------------------- loading
@pytest.fixture(scope="module")
def cfg():
    return Config.load()


def _real_region_toy(cfg, region="berkeley_small"):
    b = GraphBuilder(cfg)
    n0 = b.node(37.87, -122.27)
    n1 = b.node(37.871, -122.269)
    b.edge(n0, n1, length_m=200.0)
    return b.build(region=region)


class TestLoading:
    def test_named_pack_loads_and_takes_manifest_bbox(self, tmp_path, cfg):
        _real_region_toy(cfg).write(tmp_path / "berkeley_small")
        entry = load_named_pack(tmp_path, "berkeley_small", cfg)
        assert entry.name == "berkeley_small"
        assert entry.bbox == tuple(cfg.bbox("berkeley_small"))
        assert entry.router.pack is entry.pack

    def test_named_pack_refuses_a_dir_whose_manifest_disagrees(self, tmp_path, cfg):
        pack, _ = unprotected_left_city()          # manifest region "toy"
        pack.write(tmp_path / "berkeley_small")
        with pytest.raises(PackConfigError, match="'berkeley_small'.*'toy'"):
            load_named_pack(tmp_path, "berkeley_small", cfg)

    def test_pinned_pack_is_named_by_its_manifest_not_its_dir(self, tmp_path, cfg):
        # SR_PACK_DIR bypasses naming-by-directory: tests write packs to
        # arbitrary dirs like tmp/"bk" and tmp/"toy".
        _real_region_toy(cfg).write(tmp_path / "bk")
        entry = load_pinned_pack(tmp_path / "bk", cfg)
        assert entry.name == "berkeley_small"
        assert entry.bbox == tuple(cfg.bbox("berkeley_small"))

    def test_pinned_toy_pack_has_null_bbox(self, tmp_path, cfg):
        pack, _ = unprotected_left_city()
        pack.write(tmp_path / "anything")
        entry = load_pinned_pack(tmp_path / "anything", cfg)
        assert entry.name == "toy"
        assert entry.bbox is None


class TestRegistry:
    def test_only_returns_the_sole_entry(self, tmp_path, cfg):
        pack, _ = unprotected_left_city()
        pack.write(tmp_path / "toy")
        entry = load_pinned_pack(tmp_path / "toy", cfg)
        reg = PackRegistry([entry])
        assert reg.only() is entry
        assert reg.names() == ["toy"]
        assert len(reg) == 1
        assert reg["toy"] is entry

    def test_empty_registry_is_refused(self):
        with pytest.raises(PackConfigError, match="at least one"):
            PackRegistry([])

    def test_registry_validates_coverage(self, tmp_path, cfg):
        _real_region_toy(cfg).write(tmp_path / "berkeley_small")
        e = load_named_pack(tmp_path, "berkeley_small", cfg)
        # the same pack twice: duplicate name, and its bbox overlaps itself
        with pytest.raises(PackConfigError):
            PackRegistry([e, e])

    def test_registry_pack_for_uses_entry_bboxes(self, tmp_path, cfg):
        _real_region_toy(cfg).write(tmp_path / "berkeley_small")
        reg = PackRegistry([load_named_pack(tmp_path, "berkeley_small", cfg)])
        assert reg.pack_for((37.87, -122.27), (37.871, -122.269)) == "berkeley_small"
        assert isinstance(reg.pack_for((0.0, 0.0), (37.87, -122.27)), OriginOutside)
