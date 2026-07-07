# Vantage

[![CI](https://github.com/guillegil/vantage/actions/workflows/ci.yml/badge.svg)](https://github.com/guillegil/vantage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python >=3.11](https://img.shields.io/badge/python-%3E%3D3.11-blue.svg)](https://www.python.org/downloads/)

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
