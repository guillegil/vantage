# Command

A command someone can paste into a terminal, with a `$` prompt and a copy button.

- Use it wherever the next step is a command: `pytest --vantage` in an empty state, `vantage push` beside a queued outbox, a pytest-strategies seed to reproduce a run.
- The prompt is not copied. The text scrolls sideways inside the chip instead of wrapping, so it stays one pasteable line.
- Copy says *Copied* for a moment. If the clipboard is refused, the text is selected instead.
- *Copied* is announced to screen readers as well as shown.
- `copy={false}` drops its own copy button where a stronger control beside it copies the same text, as the run page's rerun button does.
- Print flags exactly as the tool spells them. Never shorten a flag or a seed.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
