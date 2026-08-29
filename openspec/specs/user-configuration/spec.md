# User Configuration Specification

## Purpose

Defines the namespaced, server-persisted user-preference store: one generic
table, upsert/delete semantics, reserved-key protection, and the "generic
storage, specific validation" split between storage and `vantage.service`.
`test-sections` is its first tenant.

**Component:** `vantage` — `vantage.storage` persists; `vantage.service`
validates each namespace's `value`.

## Requirements

### Requirement: Namespaced setting persistence

The system MUST persist settings in one `user_setting(namespace, key, value, updated_at)` table, primary key `(namespace, key)`.

#### Scenario: Writing a new pair creates it
- GIVEN no row exists for a given namespace and key
- WHEN a value is written for that pair
- THEN exactly one row exists for it

#### Scenario: Writing an existing pair replaces it, not duplicates it
- GIVEN a row already exists for a namespace and key
- WHEN a new value is written for the same pair
- THEN its value and `updated_at` are replaced, and no second row is created

### Requirement: Settings persist across a server restart

The system MUST persist settings durably, so a value written before a server restart reads back unchanged after it.

#### Scenario: A restart does not lose a setting
- GIVEN a setting written before a server restart
- WHEN the server restarts and the setting is read
- THEN its value is unchanged

### Requirement: Deletion is immediate

The system MUST delete a setting immediately on request, with no confirmation step server-side.

#### Scenario: A deleted setting is not read back
- GIVEN a setting exists for a namespace and key
- WHEN it is deleted
- THEN no later read for that pair returns it

### Requirement: Generic storage, specific validation

Each namespace's `value` MUST be validated by a namespace-specific model in `vantage.service` before persistence; the system MUST NOT provide a generic schema-in-value validation layer. A namespace MAY declare a key reserved for its own structural use, and the system MUST reject a write to a reserved key.

#### Scenario: A reserved key is rejected
- GIVEN a namespace that declares a key reserved
- WHEN a write targets that exact key
- THEN it is rejected and the store is unchanged

### Requirement: Port parity across storage implementations

Every storage-port method for namespaced settings MUST behave identically in the SQLite adapter and the in-memory test double.

**Verification: Test**, via the shared port-contract suite that already enforces parity for existing methods.

#### Scenario: Both adapters pass the same contract
- GIVEN the shared storage port-contract suite
- WHEN it runs against both adapters
- THEN both pass identically for every namespaced-setting method
