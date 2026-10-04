# Evidence

A traceback, captured output or log, verbatim in a sunken block that scrolls on its own and can be focused.

- `kind="traceback"` (the default) marks pytest's own conventions: `E` lines in `failed`, the `>` source line in weight 600, `path.py:N:` locations in `ink-muted`. Nothing else is highlighted.
- **Titles** name what the server recorded: *Traceback*, *Message*, *Exception repr*, *Skip reason*, *Xfail reason*, *Captured stdout* and *Captured stderr*. The captured output covers setup, call and teardown together, since the plugin joins the phases; `meta` says where the failure was (*call phase*).
- **Cut short.** The plugin and the server keep the first 64 KiB of each field and flag the cut. The block then carries the `warning` tag *truncated at capture* and `truncated` says what was kept: *Only the first 64 KiB was kept.* Never trim again in the client.
- **Not kept.** A session's failure text has a budget, spent on failed and errored results first. A field that did not fit is not a block: a `warning` `Notice` titled *Captured stderr was not kept* says why: *This session's failure text passed its 512 KiB limit, which is spent on failed and errored results first.*
- **Absent.** Say which absence it is, in a caption where the blocks would be: *Failure text was not recorded; run with `--vantage-failure-text`* when the run did not ask for it, and *Nothing was printed to stdout or stderr.* when it did and there was nothing.
- Where failure text is shown, say what it may hold in the `notice`: *may contain any value a test printed or asserted, credentials included.*
- **Hidden characters.** A character that prints nothing or reorders the text around it shows as its code point in a `warning` box, in the text and in a string `meta`; obeyed, it would make a traceback read as something it does not say. *Copy* still copies the text as recorded.
- The head carries *Wrap lines*, *Expand* (lifts the 320px cap) and *Copy*; `controls={false}` removes them. The body is focusable, scrolls on its own and always reads left to right.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
