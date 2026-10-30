# Releasing

Publishing is tag-based: pushing a `v*` tag builds the package and uploads it to PyPI through
trusted publishing (no API token is stored anywhere).

One-time setup on PyPI: Account → Publishing → add a pending publisher with
project `bostonmove`, owner `Srikarmk`, repository `bostonmove`, workflow `publish.yml`, environment `pypi`.

Each release:

1. Bump the version in `pyproject.toml`, `src/bostonmove/__init__.py` and both fields of `server.json` (the workflows refuse a mismatch).
2. Run `uv run pytest` and, since the MBTA and Bluebikes APIs change without notice, `uv run pytest -m live`. If a
   source's responses changed, re-record the fixtures with `uv run python tests/record_fixtures.py`.
3. Commit, then `git tag vX.Y.Z && git push origin main vX.Y.Z`.
4. Check the Publish run on GitHub Actions, then `uvx bostonmove@X.Y.Z --version`.

## MCP registry

`server.json` describes the server for the official MCP registry (registry.modelcontextprotocol.io). The
README's `mcp-name` comment proves to the registry that this PyPI package belongs to the listing.

The `MCP registry` workflow publishes it after each successful Publish run, signing in through GitHub
Actions OIDC, so no login is needed. It refuses to run unless `version` (both fields) in `server.json`
matches `pyproject.toml`, so bump it with the others in step 1.

To publish a version that is already on PyPI, run the workflow by hand with that tag:

```
gh workflow run registry.yml -f ref=vX.Y.Z
```
