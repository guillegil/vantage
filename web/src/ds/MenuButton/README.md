# MenuButton

A button that opens a short menu of actions: the *more* button at the end of a run's head.

- Icon only by default (`more`), named by `label`, which is also its tooltip. Pass children for a button with words.
- Items: `label`, `icon`, `onSelect`, `danger` for what destroys, and `{ separator: true }`. Destructive items come last, after a separator.
- An item a person may not use stays in the menu, `aria-disabled`, with its `disabledReason` printed under it, so the reason is found where the action is. Leave an item out only when nobody in the person's position could ever use it.
- It opens under the button, aligned to its end (`align="start"` for the other edge), or above it where the window has no room below. It is fixed to the window, so a table or panel that scrolls never clips it, and scrolling closes it. The arrow keys move, Escape closes it and returns focus to the button, and a click outside closes it.
- An item that needs confirming (*Delete run*) opens a `Dialog`; the menu itself never deletes.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
