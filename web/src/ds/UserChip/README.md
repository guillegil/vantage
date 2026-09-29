# UserChip

A person as a round monogram and their name, with *(you)* for the person looking.

- The monogram is two initials in Chivo Mono on `surface-sunken` with a `border-control` ring. There are no photos and no per-person colours: colour belongs to outcomes.
- `size="sm"` is for run rows and secondary lines; `showName={false}` keeps the name for screen readers only.
- Automated reporters are people too here: a CI runner records with its own user's token, so it has its own name and initials.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
