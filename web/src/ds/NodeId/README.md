# NodeId

A pytest node id in Chivo Mono, with the parts that identify the test in `ink` and the path in `ink-muted`.

- The file name is `ink`, the function `ink` at 600 (`t-code-strong`), and the directory, classes, `::` separators and `[parameters]` `ink-muted`. Don't restyle it.
- `truncate` keeps one line. The directory becomes `…/`, and when that is still too long the path gives way first, so the function and its `[parameters]` stay readable: `…/test_ra…::test_rail_under_load[max_load]`. The full id is always the tooltip.
- `::` inside the brackets stays part of the parameters.
- `size="lg"` sets it at 20px as the `h1` of a test or result page, a clear step above the 15px panel titles.
- Every node id links to its test history (`href`); it underlines the function on hover instead of turning `accent`, so the id stays readable.
- It is marked `translate="no"`.
- **Hidden characters.** A character that prints nothing or reorders the text around it (a bidi control such as U+202E, a zero-width character, a Hangul filler, a variation selector, a C0 or C1 control, U+FFFD where a report held U+0000) shows as its code point in a `warning` box, and is written `⟨U+202E⟩` in the tooltip. Obeyed, such characters would make one id read as another. The link still goes to the id as recorded.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
