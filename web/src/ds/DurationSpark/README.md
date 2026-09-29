# DurationSpark

A test's duration over recent runs as a small line with a faint area, its latest value, and its range.

- The line is `ink-muted`, the area `surface-sunken`, the last point an `ink` dot. Duration is data, not an outcome and not an action, so it takes no outcome or accent colour.
- The value and range use the system's duration format: `184 ms`, `4.21 s`, `1 m 52 s`.
- Consumer provides: `values` in seconds, oldest first; `null` where the test did not run is skipped.
- Use it in a test's side column and in wide tables of slow tests. Don't use it to compare tests with each other: each spark has its own scale.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
