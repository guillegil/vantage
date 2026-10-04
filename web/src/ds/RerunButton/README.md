# RerunButton

Copies a pytest command that reruns exactly the given tests: `pytest 'tests/comms/test_uart.py::test_framing_at_921600' 'tests/power/test_rails.py::test_rail_ripple[3V3]'`.

- Node ids are quoted for a POSIX shell only where they need it: brackets, spaces and quotes do.
- Past `limit` (20) node ids a command line gets unwieldy, so it offers a file instead: *Download 34 node ids*, then *pytest @new-failures.txt*, which is pytest's own way of reading arguments from a file, one per line.
- The command is the button's tooltip and its description, so it can be read before it is copied. After copying, the button says *Copied* for a moment and a polite live region says what.
- The command reruns the tests where they are; it adds no `--vantage`.
- `variant="primary"` where rerunning is the page's action, as for the run page's selected test; `keyHint` prints the key that does the same (`c`) inside the button and sets `aria-keyshortcuts`.
- Consumer provides: `nodeids`, the label as children (*Copy rerun of 2 new failures*), `limit`, `fileName`, `command` to replace the text, `variant`, `keyHint`.

## Port notes

- Ported one to one: markup, `dl-` classes, words, aria and behaviour. `window.Dotline` and `createElement` become a typed TSX export, checked against `contract.d.ts` by `contract.test-d.ts`.
- Reading arguments from a file, `pytest @new-failures.txt`, needs pytest 8.2 or later; the plugin itself supports pytest 8.0, and the command line works on every version it supports.
- The file is a `Blob`'s object URL, followed by a click on an `<a download>` that is removed at once, as the system does: no inline script, no string parsed as HTML and no request, so it runs under the page's policy and Trusted Types, and the client's link routing passes over an anchor with `download`. `shellQuote` and `rerunCommand` are exported for the queue's own commands.
