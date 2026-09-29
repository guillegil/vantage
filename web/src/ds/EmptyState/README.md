# EmptyState

What an empty list means and the one step that fills it.

- It opens with an empty dotline: a bare baseline and the cursor, waiting. When the first run arrives, the list takes the line's place.
- The title says what is missing (*No runs in bench-firmware yet*). The body is a sentence of why, or, for a project's first run, a `RecordingGuide`, which gives the exact command for this server and this person.
- For filtered lists, say that the filter matched nothing and offer to clear it; never show the first-run steps there.
- Blocks in `children` render as they are; a plain string renders as a 56-character-wide paragraph.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
