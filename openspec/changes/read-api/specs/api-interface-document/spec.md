# API Interface Document Specification

## Purpose

Defines the hand-written, machine-readable document that makes "every
documented endpoint" enumerable and checkable, replacing the framework's
self-generated document — which is the code in another format and cannot
fail a drift check against that same code. New capability; nothing
previously enumerated the service's endpoints machine-readably.

**Component:** `vantage.service` — the document is authored alongside the
routes it describes and checked by a drift test in the same package. Exact
format and file location are a design decision, not fixed here.

## Requirements

### Requirement: Machine-readable interface document

The server MUST provide a hand-written, machine-readable document
describing every documented endpoint. For every path the document declares,
requesting it with valid input MUST answer with a status in the 2xx range.
An endpoint mounted on the running application but absent from the document
MUST be reported by a drift check rather than passing silently. The
document MUST NOT be generated from the running application's route table.
The application's built-in generated interface-document routes MUST be
disabled.

**Verification: Test** — the drift criterion is assertable by comparing the
document's declared paths against the application's mounted routes.

#### Scenario: Every documented path answers 2xx
- GIVEN the machine-readable interface document
- WHEN every path it declares is requested with valid input
- THEN each answers with a status in the 2xx range

#### Scenario: A served-but-undocumented endpoint is reported
- GIVEN a route mounted on the running application but not declared in the document
- WHEN the drift check runs
- THEN it reports that endpoint as undocumented rather than passing

#### Scenario: The generated interface documents are disabled
- GIVEN a running server
- WHEN its built-in generated interface-document routes are requested
- THEN they do not answer with a generated document, and the hand-written document is what a client receives instead

#### Scenario: The document is not derived from the route table it checks
- GIVEN the machine-readable interface document and the application's mounted routes
- WHEN the drift check compares them
- THEN the document's content was authored independently of that route table, so an undocumented endpoint is a check the comparison can actually fail
