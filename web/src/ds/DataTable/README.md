# DataTable

A dense table with a sunken header row, hairline rows and right-aligned figures.

- Rows are `row-md` (36px); `dense` uses `row-sm` for side tables. Cells pad `space-3`.
- `align: 'num'` right-aligns a column in tabular Chivo Mono; use it for durations, counts and percentages.
- An empty cell prints `—` with *Not recorded* on hover.
- With no rows it prints one row saying so (`empty`). When the table is wider than its container, the wrapper becomes a focusable region named by `caption`, so a keyboard can scroll it.
- The header sticks to the top of a scrolling container. The table scrolls sideways in its own wrapper; the page never does.
- Put it in a flush `Panel`. Consumer provides `columns` (`key`, `label`, `align`, `width`, `render`) and `rows`.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
