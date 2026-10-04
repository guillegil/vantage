# Pager

The foot of a cursor-paged list: how many are shown, whether more exist, and a button to load the next page.

- The server pages by cursor and sends no total, so the pager never invents one. It says *50 runs shown, newest first. More exist.* until the server says there are none, then *All 132 runs shown.*
- The button names the page size: *Load 50 more*. While it loads, it is busy (*Loading* and the block cursor) and the list keeps its rows.
- Counts are singular or plural as needed (*The only run is shown.*), and *No runs match.* when there are none. The line is a live region, so a new count is read out without moving focus.
- `order` names the order the shown items are in, said before *More exist*: runs come *newest first* (the default), a run's results *in the order pytest reported them*.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- No page passes `order` today: the runs list and a test's history keep the default, *newest first*, and the run page pages its results inside `ChangeQueue`.
