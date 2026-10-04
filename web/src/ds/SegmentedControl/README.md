# SegmentedControl

Two to four mutually exclusive options shown at once, such as a run's visibility or a list's scope.

- The chosen option is raised on `surface`; the others are `ink-muted` on `surface-sunken`. It is a radio group: arrow keys move and choose.
- Labels are short and parallel; an icon helps only when it is the same icon the option shows elsewhere (lock for private).
- More than four options, or options that need explaining: use a select.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- The arrow keys are `arrowNav` in `lib/keys.ts`, shared with Tabs.
