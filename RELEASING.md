# Releasing

Publishing is tag-based: pushing a `v*` tag builds the package and uploads it to PyPI through
trusted publishing (no API token is stored anywhere).

One-time setup on PyPI: Account → Publishing → add a pending publisher with
project `bostonmove`, owner `Srikarmk`, repository `bostonmove`, workflow `publish.yml`, environment `pypi`.

Each release:

1. Bump the version in both `pyproject.toml` and `src/bostonmove/__init__.py` (the workflow refuses a mismatch).
2. Run `uv run pytest` and, since the MBTA and Bluebikes APIs change without notice, `uv run pytest -m live`. If a
   source's responses changed, re-record the fixtures with `uv run python tests/record_fixtures.py`.
3. Commit, then `git tag vX.Y.Z && git push origin main vX.Y.Z`.
4. Check the Publish run on GitHub Actions, then `uvx bostonmove@X.Y.Z --version`.

## MCP registry

`server.json` describes the server for the official MCP registry (registry.modelcontextprotocol.io). The
README's `mcp-name` comment proves to the registry that this PyPI package belongs to the listing. After a
PyPI release, bump `version` (both fields) in `server.json` to match, then:

```
mcp-publisher validate
mcp-publisher login github
mcp-publisher publish
```
