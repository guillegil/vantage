# RecordingGuide

How a person gets their first run into a project, built from this server's own facts: its address, whether it has accounts, and whether their role can record.

- On a server with accounts: step 1 is a token that can record (an admin makes it; the plugin reads it from `VANTAGE_TOKEN` and nowhere else), step 2 the exact command with this server's address and the project.
- On an open server there is no token step, and the guide says why.
- A person whose role cannot record (a viewer) gets no command: the guide says what their role allows and who can change it.
- The command leaves out what would change nothing: `--vantage-server` when the address is the plugin's default, `--vantage-project` for `default`.
- Under the steps, the same settings as a `pytest.ini` section, so `pytest --vantage` is enough from then on; and *Run not showing up?*, with the plugin's real rules: the typed-flag rule, the `VantageWarning`, and the outbox with `vantage push`.
- Use it in the empty run list of a project and in the project's settings. Consumer provides: `project`, `server` (the address people's machines reach, usually `location.origin`), `open`, `canRecord`.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
