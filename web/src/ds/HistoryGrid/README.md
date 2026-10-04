# HistoryGrid

Tests down, runs across, oldest to newest: the vantage view that answers *since when* and *how often*.

- Each cell uses the dotline's marks at 8px wide, so a failing streak reads as a wall and a flaky test as a comb. A test absent from a run shows a 2px speck, not an empty gap.
- The note on the right is computed from the row: *failing N runs since \<label>* when the newest result fails, *flaky · N flips in M runs* at three flips or more, otherwise the pass rate. The pass rate is passed and xfailed over everything measured, skipped left out, and never rounds up to 100% while anything is not passing.
- Consumer provides: `runs` (oldest first, each with the same `label` lists print, the first 8 characters of its id, and an optional `detail` such as branch and commit that joins it in the readout) and `rows` with `outcomes` aligned to `runs`, `null` where the test did not run.
- One row is the test page's history strip; `stacked` drops the label and puts the note under the marks for a side column. Put wide grids in a flush `Panel`; the grid scrolls sideways in its own container.
- Each row is a slider across its runs. Focus it and the arrow keys, Home and End move a cursor from run to run while the note reads that run and its outcome; the pointer does the same. Nothing lives only in a tooltip.
- A test missing from some runs says in how many it ran: *100% passing · in 12 of 30 runs*.
- A row's `note: false` keeps its note from sight where the line above already says it, as the run page's strip does for a new failure; screen readers still hear it, and the readout still shows while a cursor moves.
- **Change marks.** A row's optional `changes`, aligned to `outcomes`, carries each result's change against its run's baseline, from the server: a new failure stands above the track and a fixed result has a ring above its dot, as in `Dotline`. The readout names it: *7f3a2c1e (main at 7aa1c5d): failed, new failure*.
- **Opening a run.** Give each run an `href`, the address of this test's result in that run, and a click opens the run under the pointer, Enter the run under the cursor (the slider says so with `aria-keyshortcuts="Enter"`). `onOpen(href)` follows it in the client's router instead of loading a page. A slider holds no links of its own, so a page that shows a grid also lists its runs as links: the grid is never the only way to a run's result.
- **`fill`** (with `stacked`) gives the runs the grid's whole width, up to 28px a run with marks 24px tall, for a test's own page, where the history is the loudest thing on it.
- **Axis.** The first and last runs' labels sit under the ends of the marks while both fit apart. With too few runs for that (about a dozen), the axis says the span in words instead, *3 runs, oldest to newest*, and the readout names each run.
- The slider's name and readout show recorded text as it is: hidden characters are written `⟨U+202E⟩`.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- The client gives each run its `href`, the address of the run's result of this test, and follows it with `onOpen` in its router. The history page lists each run as a link, and the result page's strip links to it, so the grid is never the only way to a result. The client passes no `fill`.
- A row's `changes` come from each history entry's `change`, against that entry's own run's baseline.
