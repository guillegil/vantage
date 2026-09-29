# RunStatus

A run's state in a mark and words; a finished run also shows pytest's exit status.

| Shown | When | Mark |
| --- | --- | --- |
| running | No end yet, last contact within the grace period | blinking block cursor in `ink` |
| all passed · exit 0 | Finished, exit 0 | `passed` dot |
| tests failed · exit 1 | Finished, exit 1 | `failed` dot |
| interrupted · exit 2 | Finished, exit 2 | `interrupted` square |
| internal error · exit 3, usage error · exit 4 | Finished, exit 3 or 4 | `error` dot |
| no tests collected · exit 5 | Finished, exit 5 | `skipped` dot |
| interrupted | A report said the session was stopped | `interrupted` square |
| abandoned | No end, and no contact within the grace period | `abandoned` dashed ring |

- The tooltip explains `interrupted` and `abandoned` in the server's terms. A single test longer than the grace period can read as abandoned while it still runs; the wording "no contact within the grace period" keeps that honest.
- The reason is always part of the accessible name, not only the tooltip. `explain` prints it as well, for a run page's head.
- `compact` keeps only the mark and moves the words to the tooltip and screen readers; run rows use it for finished runs.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
