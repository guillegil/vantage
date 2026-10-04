# Tabs

Views of one thing, such as a result's evidence, checks, vector and history.

- Give each tab its `panel`: Tabs renders the active one as a `tabpanel`, and the tab and the panel name each other. Without panels, pass `controls`, the id of the element each tab shows.
- The active tab is `ink` at 600 over a 2px `accent` underline. Counts follow in mono; a count of failures is `failed`.
- A tab holding a view of what a plugin recorded ends in the plug icon, named *From pytest-verify*, so people know where the data comes from. Evidence, vantage's own, comes first.
- Arrow keys, Home and End move between tabs and select them; Tab moves into the panel. Tabs scroll sideways when they don't fit, and the focus ring stays inside the strip.
- A string label may be recorded text, such as a collection's name: hidden characters show as their code points.
- Don't make a tab strip of one tab: with nothing to switch to, show the content itself.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- The arrow keys are `arrowNav` in `lib/keys.ts`, shared with SegmentedControl. A label is a string, as the contract types it, so it always goes through `visible`.
