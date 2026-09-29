# Dotline

A run's results in collection order, drawn so that height is attention: a pass is a dot on the line, a failure stands up out of it.

- Marks: `passed` and `xfailed` are 3px dots, `skipped` a 1px dash on the baseline, `xpassed` half height, `failed` full height, `error` full height, broken in the middle. Shape carries the outcome as well as colour.
- One result per 4px (`dotline-pitch`). When results outnumber the width, adjacent results share a mark that shows the most severe of them, and a caption says so: *1 mark = 6 results, the most severe shown*. Set `showScale={false}` only where the tooltip is enough (run rows); the title always states it.
- A running run ends in a blinking block cursor. It stops blinking under `prefers-reduced-motion`.
- `variant="chars"` prints pytest's own progress line, one row per file with its percentage, for small runs.
- **Change marks.** Where results carry a `change` against their run's baseline, a new failure's bar stands 4px above the track, taller than a failure already known, and a fixed test's dot has a ring above it. Other changes draw nothing. The marks overflow the box upward, so a line with them takes the same room as one without. Where results share a mark, a new failure outranks a known one, and the label counts both changes.
- `width="auto"` takes the component's own width as the most the line may use, and redraws as it resizes: a run page's head. A run with fewer results than fit still draws one mark per result, so the line ends where the run does.
- `under` sets content to the line's own measure: a run page puts pytest's `SummaryLine` there, so its rules end where the line ends.
- `cursor`, a result's index, outlines that result's mark in `focus`, as `HistoryGrid` outlines its cursor: the run page marks the selected test's place in its run.
- Consumer provides: `results` in collection order (outcome strings, or objects with `outcome`, `nodeid` and `change`), a `width`, `running`.
- Don't sort the results, and don't colour the baseline: order is information, and the line is the calm state.

## Port notes

- Ported one to one, change marks included: the client passes plain outcomes until the server says what changed against a baseline.
