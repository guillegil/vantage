# BaselineNote

The line under a run's dotline that says what the run was compared with, or why it was not.

| Case | Sentence |
| --- | --- |
| Same branch | *Compared with 1adf29af on main, 42 min earlier* |
| No earlier complete run on its branch | *No earlier complete run on feat/uart-dma; compared with 0b77e4f2 on main, 3 h earlier* |
| Detached HEAD | *No branch recorded (detached HEAD at 4b8f6a3); compared with 0b77e4f2 on main, 3 h earlier* |
| Git information, but no branch or commit | *No branch recorded; compared with 0b77e4f2 on main, 3 h earlier* |
| No git information | *Recorded outside a git repository; compared with 0b77e4f2 on main, 3 h earlier* |
| Nothing earlier was complete | *Nothing to compare with yet.* |
| Still running | *Compared with its baseline once the session ends.* |
| Abandoned | *Not compared: no end was recorded.* |

- **Complete, not earlier.** A run's baseline is the latest complete run on its branch that started before it, else the project's latest complete run. A complete run finished, was neither interrupted nor stopped early, and exited 0 or 1. An earlier run that stopped early (`-x`, `--maxfail`, a collection error, Ctrl-C) or collected nothing exists, but is never a baseline, so the fallback says *No earlier complete run*, never *No earlier run*.
- `earlier` is how much sooner the baseline started, in seconds, printed as a run list prints times: *under 1 s*, *42 s*, *42 min*, *3 h*, *2 d*.
- The baseline's label is mono and links to that run, with its full id as the tooltip. A branch or commit is printed as recorded, in mono and set apart from the words around it, so neither its direction nor a character that reorders text reaches them; hidden characters show as their code points.
- `children` ends the line: the run page puts the rerun of the new failures there.
- Consumer provides: `state` (`compared`, `none`, `pending`, `abandoned`), `baseline` (`label`, `href`, `id`, `branch`), `earlier`, and `fallback` (`{ reason: 'branch', branch }`, `{ reason: 'detached', commit }`, `{ reason: 'no-branch' }` or `{ reason: 'no-git' }`) when the baseline is the project's rather than the branch's.

**Served.** The server compares each run with its baseline once, when the run gets its exit status, and serves the comparison with the run, each result's change, and the changed tests paged in queue order.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- `fmtEarlier` lives in `lib/format.ts`. The states are tested in another order than the system's (pending and abandoned before a missing baseline), which prints the same sentence for every state the contract allows.
