"""The 64 KiB text-field bound, re-exported from `vantage.ingestion.truncation`
for the plugin's contract tests, which pin the plugin's own copy to it."""

from __future__ import annotations

from vantage.ingestion.truncation import MAX_TEXT_FIELD_BYTES

__all__ = ["MAX_TEXT_FIELD_BYTES"]
