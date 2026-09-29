# Time

A timestamp: relative in lists, absolute in UTC with the zone written on hover and in detail headers.

- Relative: `38 s ago`, `12 min ago`, `5 h ago`, `3 d ago`, then the date. Absolute: `2026-09-27 09:14:03 UTC`, because that is how the server stores time.
- `mode="date"` prints the day only, for membership and export dates.
- Figures are tabular. Pass `now` when rendering on a server or in a fixed preview.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
