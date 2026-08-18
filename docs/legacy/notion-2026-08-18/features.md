# Features FT-1…FT-8 — full Notion export

> **FROZEN. NOT A WORKING DOCUMENT. SCHEDULED FOR DELETION.**
> See `README.md` in this directory first. Snapshot taken 2026-08-18.

All eight were `Proposed`, `Must Have`, Phase 1, at export time. Rows FT-9 and
FT-10 in the same Notion database belong to a different project and are not
included.

| Ref | Name | Description |
| --- | --- | --- |
| FT-1 | Session recording | The system records each pytest session as a durable entry, only when asked to. |
| FT-2 | Test capture | The system captures what happened to each test: outcome, timing, parameters, markers and the exact failure location. |
| FT-3 | Execution context | The system records the commit, machine and worker layout a session ran under, so a result can be traced back to the code that produced it. |
| FT-4 | Read API | The system serves recorded data over a read-only HTTP interface, including the history of a single test. |
| FT-5 | Web interface | The system shows a test's history over time in a browser, which is the question the whole product exists to answer. |
| FT-6 | Fault tolerance | The system degrades to doing nothing rather than disrupting the user's test suite or filling their disk. |
| FT-7 | Packaging and runtime | The plugin installs cleanly into someone else's test environment, on every supported Python, without network access. |
| FT-8 | Architecture | The domain stays free of infrastructure, so storage can be replaced and the schema never needs migrating. |

**FT-6 carried a note worth keeping:** *"The most important feature in Phase 1.
An observability tool that breaks a suite is uninstalled the same day."*

## Requirements per feature, as related in Notion

| Feature | Requirements |
| --- | --- |
| FT-1 Session recording | RQ-1, RQ-2, RQ-3, RQ-31, RQ-38, RQ-41, RQ-44 |
| FT-2 Test capture | RQ-4, RQ-5, RQ-6, RQ-7, RQ-8, RQ-9, RQ-32 |
| FT-3 Execution context | RQ-10, RQ-11, RQ-12, RQ-13, RQ-35, RQ-39 |
| FT-4 Read API | RQ-14, RQ-15, RQ-16, RQ-17, RQ-33, RQ-36 |
| FT-5 Web interface | RQ-18, RQ-19, RQ-20, RQ-34 |
| FT-6 Fault tolerance | RQ-21, RQ-22, RQ-23, RQ-37, RQ-42 |
| FT-7 Packaging and runtime | RQ-24, RQ-25, RQ-27, RQ-28, RQ-40 |
| FT-8 Architecture | RQ-26, RQ-29, RQ-30 |
