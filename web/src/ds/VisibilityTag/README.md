# VisibilityTag

Who can see a run: *Private* (lock) or *Project* (people), plus *+N* (link) when it is shared with people beyond that.

- It appears wherever a run appears: run rows, the run page header, share dialogs. A person never has to guess who else sees their results.
- The tooltip says it in full: *Visible to the person who started it and the 2 people it is shared with*.
- Text is `ink-muted` at 12px; there is no colour for private or public.

**Ahead of the server.** The read API does not serve this yet; the props below are the component's own contract, for the server to fill once it does.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
