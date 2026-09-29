# Evidence

A traceback, captured output or log, verbatim in a sunken block that scrolls on its own and can be focused.

- `kind="traceback"` (the default) marks pytest's own conventions: `E` lines in `failed`, the `>` source line in weight 600, `path.py:N:` locations in `ink-muted`. Nothing else is highlighted.
- When the plugin truncated the text, a `warning` tag says *truncated at capture* and `truncated` says what was kept. Never trim again in the client.
- Failure text is recorded only with `--vantage-failure-text`. Where it is shown, say what it may hold in the `notice`: *may contain any value a test printed or asserted, credentials included.*
- Titles use pytest's section names: *Captured stdout call*, *Captured log setup*.
- The head carries *Wrap lines*, *Expand* (lifts the 320px cap) and *Copy*; `controls={false}` removes them. The body is focusable, scrolls on its own and always reads left to right.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- A character that prints nothing or reorders the text around it (a bidirectional control such as U+202E, a zero-width character, a Hangul filler, a variation selector, a C0 or C1 control other than tab and line breaks, or U+FFFD, which the server stores where a report held U+0000) is shown as its code point, `U+202E`, in a `dl-hidden-char` box in `warning` (`port.css`). The design system does not say how evidence shows them; obeyed, they would make a traceback read as something it does not say. A `meta` given as a string, which may name a recorded path or exception type, is shown the same way. *Copy* still copies the text as it was recorded.
