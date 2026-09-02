# Region-agnostic by design, single-metro by data

The engine, safety model, and cost model carry **no region-specific assumptions**; the
system is designed to run anywhere OSM data exists. The ingestion pipeline is already
region-parameterized (`region.presets`, `build_pack --region`). We just **ship a single
metro's data** (Berkeley / North Oakland) as the current pack.

This costs nothing now — the pipeline already supports it — and is the honest bridge from
craft artifact to eventual product: when broader coverage is wanted, it is a data/ops
problem (build more packs), not an engine redesign. The explicit "no" here is the point:
no Berkeley-specific hard-coding is allowed to creep in, because it would quietly convert a
data limitation into a design one.

Note this is about *correctness across regions*, not *scale* — routing over a much larger
search space is a separate, unresolved problem (ADR-0009).
