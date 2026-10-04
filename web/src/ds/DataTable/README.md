# DataTable

A dense table with a sunken header row, hairline rows and right-aligned figures. It sorts, scrolls in a fixed height with only the rows in view in the page, and selects.

- Rows are `row-md` (36px); `dense` uses `row-sm` for side tables. Cells pad `space-3`.
- `align: 'num'` right-aligns a column in tabular Chivo Mono. A column with a `format` (`count`, `seconds`, `bytes`, `percent`, `number`, a function) formats its values and aligns them as figures unless it says otherwise.
- An empty cell prints `—` with *Not recorded* on hover.
- **Sorting.** A `sortable` column's header is a button with the header's `aria-sort`. A first press sorts figures largest first and words A to Z; the next reverses. Sorting is stable and puts empty cells last either way. Pass `sortValue` when a cell renders something other than its value. Controlled with `sort` and `onSortChange`, or started with `defaultSort`. A polite live region says the new order.
- **Height.** With `maxHeight` the table scrolls in its own box with its header stuck to the top. Past `windowAfter` rows (100) only the rows in view and a margin are in the page, so 20,000 rows scroll like 20; cells then stay on one line, cut with an ellipsis, and a foot counts the rows and says the order.
- **Selection.** `selectable="multiple"` adds a checkbox column with a select-all box (mixed while some are chosen); `selectedKeys` and `onSelectionChange`. `onRowSelect` makes each row open something: its first cell becomes a button, so the keyboard reaches it; keep that cell free of links. `selected` still marks one row.
- With no rows it prints one row saying so (`empty`). A table wider than its box, or with a height, is a focusable region named by `caption`, so a keyboard can scroll it.
- Put it in a flush `Panel`. Consumer provides `columns` (`key`, `label`, `align`, `width`, `render`, `format`, `sortable`, `sortValue`), `rows`, `rowKey` and the options above.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- The design system keeps the chosen keys in an object, so it compares a key by its text; the port keeps them in a `Set` of their text, which compares them the same way.
