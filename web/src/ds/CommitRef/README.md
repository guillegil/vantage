# CommitRef

The branch and commit a run was made from, as git printed them, with a `dirty` tag when the tree had uncommitted changes.

- Branch and sha are `t-code-small` in `ink` behind `ink-muted` icons; the sha is cut to 7 characters, full in the tooltip.
- A long branch name ends in an ellipsis within its column; the tooltip has the whole name.
- `dirty` is a `warning` tag, because results from an uncommitted tree may not reproduce from the sha.
- A commit subject is optional and truncates with its full text on hover.
- When the plugin found no repository, it says *no commit recorded*. Never leave the cell blank.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
