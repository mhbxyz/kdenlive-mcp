# Contributing

## Local quality gate

The same commands run in CI (`.github/workflows/ci.yml`):

```sh
uv sync                          # development environment from uv.lock
uv run ruff format --check .     # formatting
uv run ruff check .              # lint (includes flake8-bandit security rules)
uv run mypy                      # strict type checking of src/
uv run pytest                    # unit, protocol and packaging tests
uv run pytest -m integration     # only tests needing real Kdenlive/MLT/FFmpeg
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
| `Integration (distribution Kdenlive/MLT/FFmpeg)` | yes | the same suite restricted to `-m integration`, against Ubuntu 24.04 packages (Kdenlive 23.08.x, older than the 26.08 development baseline) |

All jobs run on `ubuntu-24.04` with `permissions: contents: read`. Actions are pinned to
commit SHAs and checkout does not persist credentials. No job uses secrets.

## Writing tests

- **Fixtures, not personal data:** never use personal media or user projects. Generate synthetic media (FFmpeg `lavfi`) or use files under `tests/fixtures/` with documented provenance.
- **Stubbing external programs:** use the `make_stub` / `stub_env` fixtures (executable stubs on a private `PATH`) to simulate a missing tool, a crash or a timeout.
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
