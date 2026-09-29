# SummaryLine

pytest's closing line: the counts in pytest's order and wording, between `=` rules in pytest's colour for the session.

- Order is pytest's: failed, passed, skipped, deselected, xfailed, xpassed, warnings, error. `error` and `warning` pluralise as pytest does.
- The rules take pytest's session colour: `failed` with any failure or error, `warning` with warnings or xpassed and nothing worse, `passed` when something passed, `warning` otherwise. Each count is in its own outcome colour.
- Duration is pytest's format: `in 48.21s`, and past a minute `in 125.31s (0:02:05)`.
- While running, the rules turn `ink-faint` and the counts read *so far*.
- When the line cannot fit on one line, it drops the rules and sets the text from the start, rather than leaving stubs of rule at the edges.
- Use it once, at the head of a run page or a run's summary panel, where it sits under the run's `Dotline` at the line's own measure (`Dotline under`). In lists, use `Dotline` with counts.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
