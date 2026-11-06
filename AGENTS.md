# AGENTS.md

## State of the repo

Rewritten from scratch on 2026-11-05 (branch `rewrite/mvp-2026-11-05`). The old
code is gone and is **not a reference**. The old tree lives at git tag
`archive/pre-rewrite-2026-11-05`; old ADRs, glossary and handoff are in
`docs/archive/` and are history only. Do not reuse their decisions unless a new
ADR re-adopts them.

## Method

Component design, in order: abstract (components, responsibilities, boundaries) ->
concrete (types, interfaces) -> properties (invariants) -> implementation. Do not
start a stage before the previous one is agreed.

Parallel implementation uses pstack `swarm` with **at most 5 workers at a time**.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/`, created lazily by
`/grill-with-docs` as terms and decisions resolve. Neither exists yet. See
`docs/agents/domain.md`.

### Vendored skills

`/grill-me` and `/grill-with-docs` (plus the `grilling` and `domain-modeling`
skills they call) are vendored from mattpocock/skills into `.claude/skills/`.
See `.claude/skills/README.md`. pstack lives in WSL under
`~/.claude/plugins/marketplaces/cursor-plugins/pstack` and is not loaded in the
desktop app.
