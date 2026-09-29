# Wordmark

The name `vantage`, lowercase, in Chivo Mono 600 at 16px (`t-wordmark`) in `ink`.

- It is the command people type (`vantage`, `vantage push`, `pytest --vantage`), so it is set like one: lowercase, monospaced, never title case.
- Vantage has no symbol. Don't draw one, and don't put the name in a box, a badge or a colour.
- In the app bar a `/` in `ink-muted` and the project switcher follow it.
- Consumer provides: `href` when the name links home.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
