# ChangeBadge

What a result did compared with its run's baseline, as the change's mark and its name: *new failure*, *still failing*, *fixed*, *new test*, *removed*, *not reached*.

- **The baseline** is the latest complete run on the same branch that started before this one, else the project's latest complete run. A complete run finished, was neither interrupted nor stopped early, and exited 0 or 1. The server decides each result's change; the client never compares two runs itself.
- **The changes.** A *new failure* failed here and did not in the baseline, including a new test whose first run failed. *Still failing* failed in both. *Fixed* failed in the baseline and does not now; its outcome mark says whether it passed or was skipped. A *new test* is new here and did not fail. *Removed* was in the baseline and was not collected here. *Not reached* was in the baseline, and this interrupted or abandoned run stopped before it: never call that removed.
- The glyph is a short piece of dotline at the dotline's own scale: a pass either side of the change's mark, which is a new failure's bar standing above the track, a still-failing bar within it, or a fixed dot with a ring above it. New, removed and not-reached tests draw nothing in a line; they take the `plus`, `circle-minus` and `square` icons.
- *new failure* is set in `failed` and *fixed* in `passed`, both semibold; *still failing* in `ink`; the rest in `ink-muted`. The words carry the change and the colour only repeats it.
- `detail` adds the sentence a detail head needs: *New failure: passed in 1adf29af*, *Still failing: 4 runs, since 1ad93e49*.
- Consumer provides: `change`, `was` (the baseline's outcome), `streak` (`{ runs, since }` for a test still failing), `baseline` (its label), `outcome` (this result's), `detail`.

**Served.** The server compares each run with its baseline once, when the run gets its exit status, and serves the comparison with the run, each result's change, and the changed tests paged in queue order.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- The six changes' words, group titles and icons are one table, `lib/changes.ts`, which HistoryGrid reads as well. `ChangeGlyph` is exported for the queue's rows and group heads, which draw the same mark.
