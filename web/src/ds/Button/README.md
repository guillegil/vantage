# Button

An action, in four variants: primary, secondary (the default), quiet and danger.

- One `primary` per view at most, for the action the view exists for (*Save rule*, *Sign in*). It is the only `accent` fill.
- `quiet` is for row and panel-head actions; `danger` only for the confirming button of a destructive action, never the button that opens the confirmation.
- Labels say what happens, in sentence case: *Load 50 more*, *Delete run*, *Copy command*. No *OK*, no *Submit*.
- `size="sm"` (28px) inside rows and panel heads, 32px elsewhere. An icon-only button needs `label`.
- `disabledReason`, not `disabled`, when a person's role or the object's state rules the action out: the button stays focusable (`aria-disabled`) and shows the reason on hover and on focus. Plain `disabled` (50%, keeps its shape) is for a moment when no one can use it.
- `busy` while a request is in flight: `aria-busy`, clicks ignored, and the terminal's block cursor after the label (`busyLabel` replaces it: *Saving*). No spinners.
- An icon-only button shows its `label` as a tooltip on hover and on focus.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
