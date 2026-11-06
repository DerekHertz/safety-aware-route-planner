# Vendored skills

Copied verbatim from [mattpocock/skills](https://github.com/mattpocock/skills)
at `c55ee46073ed923f86ce59a5eb3b6d895095d1b7` (MIT, see
`LICENSE-mattpocock-skills`), because plugin-installed skills are not usable
from the desktop app. Project skills in `.claude/skills/` are.

| Skill | Upstream path | Notes |
|---|---|---|
| `grill-me` | `skills/productivity/grill-me` | user-invoked only; delegates to `grilling` |
| `grill-with-docs` | `skills/engineering/grill-with-docs` | user-invoked only; delegates to `grilling` + `domain-modeling` |
| `grilling` | `skills/productivity/grilling` | the interview itself |
| `domain-modeling` | `skills/engineering/domain-modeling` | writes `CONTEXT.md` and `docs/adr/`; this repo already follows its format |

Refreshed 2026-11-05 from the WSL plugin copy (mattpocock-skills 1.3.1): `grilling` now
words each question so "yes" accepts the recommendation. `domain-modeling` was **not**
refreshed: 1.3.1 renamed `CONTEXT.md` to `GLOSSARY.md`, but this repo keeps `CONTEXT.md`
(see `AGENTS.md`). `.agents/skills/` (untracked) mirrors this directory.

To refresh, re-download the same paths from upstream `main` and update the SHA above.
