# Notice

A sentence the person needs before the data: info, warning or danger, on a tint with an icon.

- `info` on `surface-sunken`, `warning` on `warning-soft`, `danger` on `failed-soft`; the text stays `ink` in all three and the icon carries the tone.
- Explain an absence by its cause, and a limit by its number: *6 older runs predate `rig` and are not shown.*
- An unreachable server shows the `danger` notice in place of data, never a stale copy.
- A session that ended keeps the page as it was and says so, with signing in as the action: *Your session ended at 21:14 UTC. Sign in again to carry on; this page stays as it is.*
- One notice per cause. No left rails, no dismissible banners for things that are still true.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
