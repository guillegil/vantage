# MetaList

Key and value pairs in two mono columns: session metadata, a plugin's facts, a vector's arguments.

- Keys are `ink-muted` at 12px, values `ink` at 13px; both Chivo Mono, because both were printed by code.
- A missing value is `—` with *Not recorded* on hover. Never guess one.
- `source` tags where a value came from when it matters: `--vantage-metadata`, a plugin name.
- Consumer provides: `items` of `{key, value, source}` in the order they were recorded.
- Keys and values are recorded text: a character that prints nothing or reorders text shows as its code point (see `NodeId`).

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
