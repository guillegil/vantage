# RoleBadge

A person's role in a project: owner, editor or viewer.

- A viewer reads the project's runs; an editor also records runs and edits sections; an owner also manages members. Server admins see every project and manage its members with or without a role in it.
- Neutral on purpose: an outlined chip in `ink` with an `ink-muted` icon (key, pencil, eye). A role is not a status and gets no colour.
- Words are sentence case: *Owner*, *Editor*, *Viewer*. Where the role can be changed, a select replaces the badge.
- `role={null}` renders nothing. An open server checks no role, and a badge would claim one nobody holds.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
