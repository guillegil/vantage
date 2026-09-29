# Field

A labelled input, select or textarea with an optional hint and error.

- Labels are `t-label` in `ink` above the control, sentence case. Hints and errors sit below, the error in `danger` with the `alert` icon, joined to the control by `aria-describedby`.
- `mono` for anything typed as code: usernames, metadata keys and values, marker expressions, server addresses.
- Controls are 32px on `surface` with a `border-control` edge; `size="sm"` selects are 28px for rows.
- Without `id`, each field gets a unique one, so two *Role* fields on one page never share a label. An `error` is read out when it appears.
- Errors say what is wrong and how to fix it: *No user named lpakr. Check the spelling or ask them for their username.*

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
