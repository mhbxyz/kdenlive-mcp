# Releasing

Releases are automated by `.github/workflows/release.yml`. They start when a maintainer pushes
a `v*` tag; only administrators can create one, and tags are immutable (#31).

## Accounts and publishers (one-time, done 2026-11-08)

- **Distribution name:** `mcp-kdenlive`. The PyPI name `kdenlive-mcp` belongs to someone else (#24).
- **Pending Trusted Publishers** for project `mcp-kdenlive` were registered by the owner's PyPI and TestPyPI account (`mhbxyz`), both with workflow `release.yml`:
  - **TestPyPI:** repository `mhbxyz/kdenlive-mcp`, environment `testpypi`;
  - **PyPI:** repository `mhbxyz/kdenlive-mcp`, environment `pypi`.

  A pending publisher does **not** reserve the name: the first successful upload creates the project. After that, the publisher becomes a normal Trusted Publisher.
- **GitHub environments:** `testpypi` and `pypi` accept deployments from `v*` tags only. There are no reviewers (autonomous flow, see `docs/governance.md`).
- **No tokens:** no API token is stored anywhere. Publishing uses short-lived OIDC credentials.

## Pipeline

```text
tag vX.Y.Z[rcN] ─► build (once)
                   ├─ check_version.py TAG     tag == version, not .dev, server.json, CHANGELOG section
                   ├─ uv build · check_dist.py · install wheel AND sdist in clean venvs · smoke test
                   ├─ build provenance attestation (GitHub)
                   └─ upload artifact "dist" (+ sha256 list)
                 ─► testpypi        publish the same files (OIDC, environment testpypi)
                 ─► verify-testpypi download from TestPyPI, check it's byte-identical to the build,
                                    install with --no-deps, dependencies from PyPI only, smoke test
                 ─► pypi            final tags only: publish the same files (OIDC + PEP 740 attestations)
                 ─► github-release  notes from CHANGELOG + verification statement + sha256 + links
```

- **Pre-release tags** (`a`, `b`, `rc`) stop after TestPyPI and create a GitHub *pre-release*. Use them to rehearse.
- **TestPyPI isolation:** the TestPyPI check never uses TestPyPI to resolve dependencies. There is no `--extra-index-url`, which would allow dependency confusion.

## Maintainer checklist

1. **Changelog:** move the `[Unreleased]` entries to `## [X.Y.Z] - YYYY-MM-DD`. Mark breaking changes and deprecations (`docs/versioning.md`).
2. **Version:** set `version` in `pyproject.toml` (and `server.json`, #33) to `X.Y.Z` in the same PR. CI runs `check_version.py`.
3. **Merge** the PR with green CI.
4. **Rehearse (optional):** tag and push a pre-release: `git tag -a vX.Y.ZrcN -m "…" && git push origin vX.Y.ZrcN`. That pre-release needs the version `X.Y.ZrcN` in a commit on `main`.
5. **Tag the release:** `git tag -a vX.Y.Z -m "mcp-kdenlive X.Y.Z"` on the merged commit, then `git push origin vX.Y.Z`.
6. **Watch the workflow.** If any step fails before `pypi`, nothing reached PyPI. Fix the cause and release a **new version number**; tags cannot be moved.
7. **Verify:**
   - `pipx install mcp-kdenlive==X.Y.Z` in a clean environment;
   - `kdenlive-mcp --version`;
   - `kdenlive-mcp diagnose`;
   - the PyPI page shows the attestation;
   - the GitHub release lists artifacts and checksums.
8. **Back to development:** set `main` to the next `.devN` version.

## Mistakes and incidents

- **Published versions are never replaced.** PyPI refuses re-uploads of a version, and so do we.
- **A broken release:** *yank* it on PyPI (Manage → Releases → Yank, with a reason), publish `X.Y.Z+1`, and note it in the changelog. Do not delete it: deletion is irreversible, frees nothing (the number stays burned) and removes the evidence.
- **A malicious or compromised release:** follow `docs/incident-response.md`.

## Visibility

The repository is public. A PyPI release makes the wheel and sdist public and permanent (sources,
tests and docs included; `check_dist.py` keeps out the research lab and anything that looks like a
secret). The owner approved public PyPI releases for this project on 2026-11-08 (autonomous
release instructions); the first public version is 0.1.0.
