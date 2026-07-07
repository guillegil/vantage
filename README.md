# Vantage

A web platform and API for pytest -- launch, monitor live, schedule, and
compare test runs from a single vantage point.

## Status

Early scaffolding. See `docs/architecture.md` for design notes.

## Development

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
pytest
ruff check .
mypy src
```
