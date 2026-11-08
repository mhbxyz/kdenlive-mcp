# Contributing

## Local quality gate

The same commands run in CI (`.github/workflows/ci.yml`):

```sh
uv sync                          # development environment from uv.lock
uv run ruff format --check .     # formatting
uv run ruff check .              # lint (includes flake8-bandit security rules)
uv run mypy                      # strict type checking of src/
uv run pytest                    # everything; tests needing missing programs are skipped
uv run pytest -m integration     # only tests needing real Kdenlive/MLT/FFmpeg
uv run pytest -m e2e             # only end-to-end tests (tests/e2e)
uv run pytest --cov              # with line and branch coverage
uv build && uv run --no-project python scripts/check_dist.py dist
```

To smoke-test a built artifact the way CI does:

```sh
uv venv /tmp/kmcp && VIRTUAL_ENV=/tmp/kmcp uv pip install dist/*.whl
(cd /tmp && /tmp/kmcp/bin/python -I "$OLDPWD/scripts/smoke_installed.py")
```

## CI jobs

| Job | Required | What it proves |
|---|---|---|
| `Lint and type check` | yes | ruff format, ruff lint, mypy strict |
| `Tests (Python 3.11 / 3.12 / 3.13 / 3.14)` | yes | unit and stdio protocol tests. Kdenlive, MLT and FFmpeg are absent here, so integration tests are **skipped with a reason** (visible through `-ra`). |
| `Build and smoke-test artifacts` | yes | wheel and sdist build; `check_dist.py`; clean install of each artifact plus a stdio MCP round trip |
| `Integration (distribution Kdenlive/MLT/FFmpeg)` | yes | the suite restricted to `-m "integration or e2e"`, against Ubuntu 24.04 packages (Kdenlive 23.08.x, older than the 26.08 development baseline) |

All jobs run on `ubuntu-24.04` with `permissions: contents: read`. Actions are pinned to
commit SHAs and checkout does not persist credentials. No job uses secrets.

## Writing tests

- **Fixtures, not personal data:** never use personal media or user projects. Generate synthetic media (FFmpeg `lavfi`) or use files under `tests/fixtures/` with documented provenance.
- **Stubbing external programs:** use the `make_stub` / `stub_env` fixtures (executable stubs on a private `PATH`) to simulate a missing tool, a crash or a timeout.
- **Three levels:**
  - **unit** (no marker): XML, timeline and policy logic, with external programs replaced by stubs. They run on every supported Python.
  - **integration** (`@pytest.mark.integration`): real `melt`, `ffprobe`, `kdenlive`.
  - **e2e** (everything under `tests/e2e/`, marked automatically): the real server started over stdio by an MCP client, driving tools the way an assistant does, then checking the rendered file objectively (streams, duration, audio levels, pixel colors of extracted frames). Helpers are shared with the demos in `examples/demos/demolib.py`. E2E tests that need Kdenlive 26.08 skip cleanly elsewhere.
- **Coverage:** CI measures line and branch coverage (`pytest-cov`, subprocesses included) and fails below the floor in the `Tests (Python 3.13)` job. The floor only goes up. New modules must reach 90 %.
- **Integration marker:** mark tests that need real programs with `@pytest.mark.integration` and skip them explicitly when the program is missing (`pytest.mark.skipif(..., reason=...)`). A skipped test is not a passed test: CI prints skip reasons.
- **Timeouts:** every test has a 120 s timeout (`pytest-timeout`). Tests that start processes must clean them up (`mcp_kdenlive.process.run` kills the whole process group).
- **No secrets:** do not print secrets or environment dumps in test output.

## Flaky tests

A test that fails intermittently is a bug:

1. Open an issue with the failing job log.
2. Fix it, or mark it `@pytest.mark.skip(reason="flaky: #<issue>")` in the same PR that opens the issue.
3. Never retry silently in CI.

## Automated CI vs manual Kdenlive checks

CI cannot open the Kdenlive GUI. Every feature that writes a project also needs a **manual
checklist** in its issue (open, edit, save, reopen, render in the target Kdenlive version).
Until someone has performed it, the item is listed as a known limitation. The research lab
in `research/lab/` can automate parts of this locally; it is not part of CI.

## Releases

Maintainers follow [docs/releasing.md](docs/releasing.md):

- update the changelog;
- set the version in `pyproject.toml` and `server.json`;
- merge;
- tag `vX.Y.Z`.

The release workflow publishes to TestPyPI, verifies the result, publishes to PyPI and the MCP
Registry, then creates the GitHub release. Release authority and protections:
[docs/governance.md](docs/governance.md).
