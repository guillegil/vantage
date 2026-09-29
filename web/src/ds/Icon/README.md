# Icon

The system's own stroke icons: a 16-unit grid, a 1.5 stroke, round caps and joins, drawn in `currentColor`.

- Put an icon beside a word. An icon-only control is a `Button` with `icon` and `label`; the label becomes its accessible name and its tooltip.
- Pass `title` only when the icon alone carries meaning; it then becomes `role="img"`.
- Sizes: 16 by default, 14 in badges, chips and dense rows, 12 inside 20px tags.
- The outcome glyphs (`check`, `cross`, `alert`, `dash`, `circle-cross`, `circle-check`) take outcome colours only when they mean an outcome. Where `check` means "copied" or "ready", it stays in the text colour around it.
- `pin` fills when pressed; every other icon is stroke only.
- There is no icon font and no emoji. Extend the set in the same geometry.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
