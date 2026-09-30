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

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- Adds an optional `href` to each run, the address of that run's result of the test, and `onOpen`, which follows it in place of loading a new page. Where runs carry one, a click opens the run under the pointer, and Enter the run under the cursor, which the slider announces with `aria-keyshortcuts="Enter"`. The design system reads history cells but does not say how to open one; a slider holds no links of its own, so the grid is never the only way to a run's result: the client's history page lists each as a link. Without `href` the grid behaves as designed.
- The slider's accessible name writes a character that prints nothing or reorders the text around it in the node id as its code point, `⟨U+202E⟩`, as `NodeId`'s tooltip does. The design system does not say how a name shows them; obeyed, a right-to-left override would make the name read as another test's.
- A row's `changes` come from each history entry's `change`, against that entry's own run's baseline.
