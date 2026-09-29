# OutcomeBadge

An outcome as glyph and word on its soft tint, optionally with a count.

- Words are pytest's own, lowercase: `passed`, `failed`, `error`, `skipped`, `xfailed`, `xpassed`. A count pluralises `error` the way pytest does (`2 errors`).
- `passed`, `failed`, `error` and `xpassed` sit on their `-soft` tints; `skipped` and `xfailed` sit on `surface-sunken`, because they ask for nothing.
- Use it for one result's outcome (a result header, a vector row). For a run's totals use `SummaryLine`.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
