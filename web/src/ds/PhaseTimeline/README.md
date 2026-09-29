# PhaseTimeline

One result's setup, call and teardown as proportional segments with their durations.

- Setup and teardown are `ink-faint`, call `ink-muted`; a phase that failed or errored takes that outcome's colour and says so in words below.
- Every segment keeps at least 3px, so a 2 ms setup is still visible beside a 40 s call.
- Consumer provides: `phases` in order, each with `name`, `seconds` and, when it did not pass, `outcome`.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
