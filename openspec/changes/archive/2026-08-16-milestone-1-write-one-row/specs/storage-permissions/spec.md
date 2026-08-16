# Storage Permissions Specification

## Purpose

Defines the owner-only file permissions the server MUST enforce on what it
creates on disk: the database file and the artefact store directory. New
capability for this replan — no previous spec covered it.

**Component:** `vantage` (server) — `vantage.storage`, at database and store
creation time.

## Requirements

### Requirement: Owner-only store permissions (RQ-40)

The server SHOULD create the database file and the artefact store readable
and writable only by the user account that created them.

#### Scenario: Database file mode
- GIVEN a POSIX machine with a permissive umask of 022
- WHEN a database is created
- THEN its mode is 0600

#### Scenario: Artefact store directory mode
- GIVEN a POSIX machine with a permissive umask of 022
- WHEN the artefact store directory is created
- THEN its mode is 0700

#### Scenario: Existing permissive database is still recorded, with a warning
- GIVEN an existing database whose mode is 0644
- WHEN a session records to it
- THEN the run is recorded and a warning names the permissive mode
