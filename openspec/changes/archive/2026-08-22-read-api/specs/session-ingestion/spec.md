# Delta for Session Report Ingestion

## ADDED Requirements

### Requirement: Ingestion endpoints excluded from the read-only surface

The session-report and heartbeat ingestion endpoints MUST NOT be counted
among the document-declared read paths that `history-read-api`'s read-only
guarantee covers. Recording the boundary here, alongside the endpoints that
write, keeps "read-only" scoped by the endpoints that actually read rather
than asserted only in the capability that benefits from the exclusion.

**Verification: Inspection** — a scope assertion checked against what the
machine-readable interface document declares as a read path versus a write
path, not a behavior that can fail a runtime assertion on its own.

#### Scenario: Ingestion endpoints are marked as writing, not reading
- GIVEN the machine-readable interface document
- WHEN its entries for the session-report and heartbeat ingestion endpoints are inspected
- THEN both are marked as endpoints that write, and neither appears among the paths covered by the read-only guarantee
