# OutcomeMark

One outcome as its glyph in its colour, with the outcome word as its accessible name.

| Outcome | Glyph | Colour | Dotline mark | pytest char |
| --- | --- | --- | --- | --- |
| passed | `check` | `passed` | 3px dot on the line | `.` |
| xfailed | `circle-cross` | `xfailed` | 3px dot on the line | `x` |
| skipped | `dash` | `skipped` | 1px dash on the line | `s` |
| xpassed | `circle-check` | `xpassed` | half height | `X` |
| failed | `cross` | `failed` | full height | `F` |
| error | `alert` | `error` | full height, broken in the middle | `E` |

- Use it where a word would crowd: check rows, table cells, tooltips. Beside running text, use `OutcomeBadge`.
- Never draw an outcome by colour alone. The glyph is what tells `xpassed` from `failed` under red-green colour blindness.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
