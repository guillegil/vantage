# RetentionTag

How long a run stays: *kept*, *kept · pinned*, or *deleted in N d* with the rule that will delete it on hover.

- Say *deleted*, not *archived* or *expires*: the run and its evidence go.
- Within seven days the tag turns `warning`; before that it is plain `ink-muted` text.
- The tooltip quotes the rule as the project wrote it (see `RuleSentence`).
- In a run list, *kept* is left out: a row shows the tag only when a pin holds the run or a rule will delete it. On a run's own page, *kept* is said outright.
- The rule is also part of the tag's accessible name, not only its tooltip.
- A run's own page adds the date, in UTC: *deleted in 29 d, on 2026-10-26*. Pass `date`.
- Consumer provides: `state` (`kept`, `pinned`, `expires`), `days`, `rule`, `date` on a run's page.

**Ahead of the server.** The read API does not serve this yet; the props below are the component's own contract, for the server to fill once it does.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
