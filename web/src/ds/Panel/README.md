# Panel

The container for recorded data: `surface`, a `border` hairline, `radius-md`, and a head with a title and actions.

- Panels stack `space-5` apart on `page` and pad `space-4`; `flush` removes the padding for tables, run lists and exports.
- The title is `t-heading`. Actions in the head are `sm` buttons, quiet unless one is the panel's purpose.
- `level` sets the title's heading level (default 2), so a page keeps one outline: a sign-in page's only panel is its `h1`.
- Don't nest panels, and don't give them shadows. What floats is a menu or a dialog.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
