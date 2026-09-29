# Tooltip

A short explanation for one control, shown on hover and on keyboard focus, and linked to the control by `aria-describedby`.

- Use it for the reason a control is unavailable (*Editors and owners can pin runs*) and for the name of an icon-only button. `Button` and `PinButton` add it themselves, from `disabledReason` and `label`.
- Its child must be focusable. Put nothing in it that a person acts on: no links, no buttons. A fact someone needs without hovering or focusing belongs on the page.
- It sits above the control, or below it near the top of the viewport, fixed-positioned so no panel or scroll container clips it. It hides on Escape, on scroll, and when focus or the pointer leaves.
- `describe={false}` when the text only repeats the control's accessible name, so a screen reader doesn't read it twice.
- `ink` fill with `page` text at 12px: at least 13:1 in both themes.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
