# PinButton

A toggle that pins a run to the top of its project's list.

- Unpinned it is an `ink-faint` outline that darkens on hover; pinned it is a filled `ink` pin. `aria-pressed` carries the state and the label says what a press will do: *Pin run*, *Unpin run*.
- When the person's role does not allow pinning, pass `disabledReason`: the button stays focusable, its outline turns dashed at full contrast, and the reason shows on hover and on focus.
- The pin says nothing about retention by itself. `RetentionTag` says whether a pin is what keeps a run.
- Where pins are not stored, render none: `RunList` without `onPin` keeps the column empty instead.

**Ahead of the server.** The read API does not serve this yet; the props below are the component's own contract, for the server to fill once it does.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
