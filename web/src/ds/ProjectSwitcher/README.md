# ProjectSwitcher

The project name in the app bar, and the menu of the projects the person can see, with their role in each.

- A server admin sees every project; everyone else sees `default` and the projects they are a member of.
- Each row shows the project and the person's `RoleBadge` there. On an open server roles are `null`, and rows show names only.
- The current project has a check and `accent-soft`. *New project* sits last, after a separator, and only when `canCreate` is not false and `onCreate` is given. Only server admins create projects.
- `label` names the menu (*All projects* for an admin, who sees every project). With no projects it says so, and who can add you.
- A long project name ends in an ellipsis in the button; the tooltip has the whole name.
- The menu floats with `shadow-overlay` on `surface`. It closes on Escape, on a click outside or after a choice; arrow keys, Home and End move between items, and opening it from the keyboard focuses the first one.
- `tracks` is deprecated: runs join a project by `--vantage-project`, not by metadata. It still prints under the name when given.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
