# AppBar

The top bar: wordmark, project switcher, the project's sections, node id search and the account menu.

- 48px (`topbar-height`) on `surface` with a `border` hairline. The current section is `ink` at 600 with a 2px `accent` underline.
- Everything below the bar belongs to the project named in it. Switching project keeps the section.
- Search looks up node ids across the project.
- **It adapts to its own width.** From 960px the sections sit in the bar and search is a field. Narrower, the sections fold into one menu button labelled with the current section, and search becomes an icon that opens a full-width search row; Escape closes the row and returns focus to the icon. Below 520px the slash goes, the project name shortens and the sections button keeps only its icon. Everything stays reachable at 360px.
- **Account.** With `user` and `account`, the monogram button opens a menu: the `note` first, saying whose session it is and when it ends in UTC (*Signed in as alice until 21:14 UTC*), then the `items`: *Account* (`user`), for an admin *Administration* (`shield`), a `{ separator: true }`, and *Sign out* (`log-out`). Administration is server-wide, not a project's, so it lives here and never in the project's sections; only admins get the item. The menu holds nothing about other people. With a `user` but no `account`, the monogram shows without a menu.
- **Outside a project.** The account and administration pages belong to no project: pass no `project` and no `nav`, and `search={false}`, since search looks within a project.
- **Open server.** A database made by the plugin's local store is served with no accounts. Pass no `user`: no account button renders, and its projects carry `role: null`, so no badges either. Never offer *Sign in* there.
- **New project.** `canCreate` and `onCreate` pass through to `ProjectSwitcher`. Only server admins create projects: pass `canCreate` from the admin flag, and the item stays hidden for everyone else.
- Menus close on Escape, on a click outside and after a choice; arrow keys, Home and End move between items.
- Consumer provides: `project`, `projects`, `nav`, `active`, and on a server with accounts `user` and `account`.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
