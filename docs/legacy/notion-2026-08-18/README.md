# Notion export, 2026-08-18 — FROZEN, SCHEDULED FOR DELETION

**Do not plan from anything in this directory. Do not edit it. Do not treat any
statement here as current.**

Vantage no longer uses Notion. Requirements, features and open questions are
authored in **OpenSpec** (`openspec/`) from now on, with **Engram** carrying the
session memory. This directory is a one-way, one-time dump taken on
**2026-08-18**, the day Notion was cut off, so that the reasoning behind the
first 43 requirements is not thrown away along with the tool that held it.

## Why it exists

Only 16 of the 43 requirements had ever been mirrored into the repository, in
`specs/requirements.md`. The other 27 — the whole of Milestone 2 and beyond,
including `RQ-44` — existed nowhere else. Cutting Notion without this dump would
have destroyed them.

What is worth migrating is not the requirement statements, which are easy to
rewrite. It is the **rejected alternatives** and the **change logs**: they exist
because somebody already tried the obvious thing and found out why it does not
work. `requirement-notes.md` is the file that carries them.

## When it goes away

**Delete this whole directory once its content has been migrated into OpenSpec.**
That is the intended end state, not a maintenance burden to carry indefinitely.
Nothing links to it, nothing depends on it, and nothing keeps it accurate.

Suggested order of migration, cheapest value first:

1. ~~`open-questions.md`~~ — **done 2026-08-18.** Migrated to
   `docs/open-questions.md` and answered; eight of nine closed, OQ-9 still open.
2. `requirement-notes.md` — the rejected alternatives. Fold each into the
   OpenSpec requirement it belongs to as it gets rewritten.
3. `requirements.md` — statements, criteria and rationale. Superseded the moment
   the equivalent OpenSpec requirement is written.
4. `features.md` and `project.md` — mostly already reflected in `CLAUDE.md`.

## Contents

| File | What |
| --- | --- |
| `requirements.md` | All 43 requirements: statement, priority, type, EARS pattern, verification method, acceptance criteria, rationale |
| `requirement-notes.md` | Per-requirement design notes, **rejected alternatives**, per-requirement open questions, change logs |
| `features.md` | FT-1…FT-8 and which requirements hang from each |
| `project.md` | Product reasoning, roadmap, glossary, and the historical decisions table |

## Known defects in this content

Read these before trusting anything here.

- **The identifiers run `RQ-1` to `RQ-44` and there is no `RQ-43`.** Forty-three
  requirements, not forty-four.
- **Every requirement was `Draft`.** None had earned `Approved`. Treat every one
  as open to amendment, not as settled.
- **RQ-44's acceptance criterion 2 is wrong** and was never fixed in Notion. It
  measures the grace period from the run's *start*, which no constant can
  repair. The correction is written out in full in `requirement-notes.md` under
  RQ-44 — **carry it forward when that requirement is rewritten.**
- **Verification paths are stale.** They name `packages/vantage-pytest/`,
  `packages/vantage-core/`, `packages/vantage-service/` — distributions ADR-4
  replaced with `packages/pytest-vantage/` and `packages/vantage/`. Read them as
  intent, not as paths.
- **RQ-24's page still described the plugin as depending on `vantage-core` and
  `vantage-storage`.** ADR-9 ended that; the plugin depends on pytest and the
  standard library and nothing else.
- **`project.md`'s decisions table is history, not instruction** — the page said
  so itself. Two of its rows are known wrong: "four packages" and
  "Notion is the source of truth".
- **RQ-31 and RQ-37 had their atomicity checkbox left unticked**, meaning their
  authors knew a second obligation was hiding in the statement.
