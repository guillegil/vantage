# Architecture Notes

This file collects design notes that don't yet have a home elsewhere. See
`sdd/platform/design` for the full technical design.

## Planned: frontend static asset packaging (not active yet)

The `frontend/` directory (React + Vite) does not exist yet -- it is designed
in the `gui-v1` slice. Once it exists, the built assets will be wired into
the wheel via hatchling's `force-include`, mapping:

```
frontend/dist -> vantage/server/static
```

`frontend/dist` will be gitignored; CI must build the frontend (`npm --prefix
frontend run build`) before `hatch build` so the wheel picks up the compiled
assets. This is a **config addition only** to `[tool.hatch.build.targets.wheel.force-include]`
in `pyproject.toml` -- it does not require restructuring the package layout
established in slice 0.

No `force-include` entry is added in this slice because the source directory
does not exist yet; adding the mapping now would silently produce an empty
package-data entry.
