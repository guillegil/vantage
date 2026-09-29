# RunList

A project's runs as rows: pin, state and id, who and when, the dotline and counts, commit, visibility and retention, duration.

- Pinned runs sit in their own group above the rest; everything else is newest first.
- A header row names the columns; it hides below 760px, where rows stack. *Pinned* and the rest are separate lists named by their visible headings, so a screen reader hears the grouping too.
- **Run ids.** The API's run ids are 32 hex characters. Pass `label` as the first 8 (`id.slice(0, 8)`), printed in mono as the row's link in `accent`; the full `id` is the link's tooltip and belongs in its `href`. Without `label` the full id prints and truncates with an ellipsis.
- Finished runs show only their `RunStatus` mark; running, interrupted and abandoned runs also show the word.
- The dotline is 200px with its scale in the tooltip, and draws its results' change marks. Counts follow pytest's order and colour only the figures.
- **Change counts** come first when a row has `changes`: *2 new · 1 fixed*, then a hairline, then the outcome counts. *New* counts new failures only; new tests are left to the run page. The full words and the baseline are the counts' tooltip and accessible name: *2 new failures and 1 fixed, compared with 1adf29af*.
- Visibility and retention sit together: who can see this run, and how long it stays. A run no rule will delete shows no retention tag in the list; a tag appears when a pin holds the run or a rule will delete it.
- **Pins.** With `onPin`, each row starts with a `PinButton`. Without it, no pin buttons render and the first column stays as an empty 28px cell, so every row keeps the same grid. The server stores no pins yet: leave `onPin` off until it does.
- **It adapts to its own width, not the window's.** From 1000px every fact has its own column. From 740px commit and visibility share one column on two lines. Narrower, each row stacks beside its pin and the header row goes. A name and time that don't fit wrap rather than overlap.
- Put the list in a flush `Panel`, with a `Pager` under it.
- Consumer provides `runs` (see `RunItem` in the types), `now`, and `onPin` where pins exist. With no runs it shows a plain empty state; pass `empty` with an `EmptyState` holding a `RecordingGuide` so a project's first run gets the exact command.

**Ahead of the server.** The read API does not serve visibility, pins, retention or change yet; the props below are the component's own contract, for the server to fill once it does.

## Port notes

- Ported one to one. The client passes no `onPin`, `retention` or `changes`, which the server does not store, and prints each run by its `label`, the first 8 characters of its id.
