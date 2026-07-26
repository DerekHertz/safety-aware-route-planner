"""Pack write -> load roundtrip: exact array equality and dtype enforcement."""
import numpy as np

from pyref.graph import Control, GraphPack, RoadClass
from tests.helpers.toy_graphs import GraphBuilder


def _sample_pack():
    b = GraphBuilder()
    a = b.node(37.87, -122.27, control=Control.SIGNAL_PERMISSIVE)
    c = b.node(37.871, -122.27)
    d = b.node(37.871, -122.269, control=Control.STOP_4WAY)
    e = b.node(37.87, -122.269)
    b.edge(a, c, road_class=RoadClass.primary, speed_kph=56, lanes=2)
    b.edge(c, d, length_m=250.0)
    b.edge(d, e, oneway=True)
    b.edge(e, a, road_class=RoadClass.tertiary)
    return b.build()


def test_roundtrip_exact(tmp_path):
    pack = _sample_pack()
    pack.write(tmp_path / "toy")
    loaded = GraphPack.load(tmp_path / "toy")
    for name in GraphPack.array_fields():
        a = getattr(pack, name)
        b = getattr(loaded, name)
        assert a.dtype == b.dtype, name
        np.testing.assert_array_equal(a, b, err_msg=name)
    assert loaded.meta["region"] == "toy"
    assert "config_hash" in loaded.meta


def test_dtypes_are_explicit():
    pack = _sample_pack()
    assert pack.edge_length_m.dtype == np.float64
    assert pack.edge_speed_mps.dtype == np.float64
    assert pack.edge_tail.dtype == np.int32
    assert pack.turn_ptr.dtype == np.int32
    assert pack.turn_maneuver.dtype == np.uint8
    assert pack.node_osmid.dtype == np.int64


def test_oneway_has_no_reverse():
    pack = _sample_pack()
    oneway_edges = np.flatnonzero(pack.edge_reverse == -1)
    assert len(oneway_edges) == 1  # exactly the single one-way segment
    two_way = np.flatnonzero(pack.edge_reverse >= 0)
    # reverse-of-reverse is identity
    assert np.all(pack.edge_reverse[pack.edge_reverse[two_way]] == two_way)
