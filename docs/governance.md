# Repository governance and release authority

## Who can do what

| Action | Who | Enforced by |
|---|---|---|
| Merge to `main` | anyone with write access, **through a pull request only**, squash merge, after the 8 required CI checks pass | ruleset *main: CI required, no force push* |
| Push directly to `main`, force-push, delete `main` | nobody | same ruleset (no bypass actors) |
| Create a release tag `v*` | repository administrators (today: the owner, `mhbxyz`) | ruleset *release tags v\*: only admins create* (admin bypass) |
| Move or delete a release tag | nobody, administrators included | ruleset *release tags v\*: immutable (no bypass)* |
| Publish to TestPyPI / PyPI | the `release.yml` workflow, only for `v*` tags, through the `testpypi` / `pypi` environments | environment deployment policy (tags `v*` only) + PyPI Trusted Publisher bound to `mhbxyz/kdenlive-mcp`, `release.yml`, environment name |
| Change security settings, rulesets, environments, publishers | repository owner | GitHub permissions; PyPI account ownership |
| Run CI for an outside contributor's PR | after a maintainer approves the run | Actions setting: approval required for all external contributors |

### Required status checks on `main`

- `Lint and type check`
- `Dependency audit`
- `Tests (Python 3.11)`, `Tests (Python 3.12)`, `Tests (Python 3.13)`, `Tests (Python 3.14)`
- `Build and smoke-test artifacts`
- `Integration (distribution Kdenlive/MLT/FFmpeg)`

CodeQL runs too but is not required.

## Why no human approval is required (and how to harden later)

The owner asked for an autonomous workflow: an agent opens a PR, waits for CI and merges it,
then tags and publishes releases. Required approvals would block this. GitHub does not let a PR
author approve their own PR, and the agent acts with the owner's account. So:

- **Pull requests:** they require **0 approvals**.
- **Environments:** they have **no required reviewers and no wait timer**.

What still protects the project:

- every change goes through a PR with green CI;
- `main` history cannot be rewritten;
- release tags are admin-only and immutable;
- publishing happens only from tags, in tag-restricted environments, with OIDC (no tokens).

To harden later, for example when a second maintainer joins:

1. **Code-owner review:** in the `main` ruleset, set `required_approving_review_count: 1` and `require_code_owner_review: true`. `.github/CODEOWNERS` already lists owners.
2. **Approval before publishing:** add required reviewers (and *prevent self-review*) to the `pypi` environment.
3. **Up-to-date branches:** enable `strict_required_status_checks_policy` so PRs must be current with `main` before merging.

## Untrusted pull requests

- **No privileged triggers:** workflows do not use `pull_request_target` and expose no secrets to PRs. `GITHUB_TOKEN` defaults to read-only, and Actions cannot approve PRs.
- **Approval for outside contributors:** fork PRs from outside contributors wait for a maintainer to approve the CI run. Review changes to `.github/`, `pyproject.toml`, `uv.lock` and `scripts/` carefully before approving: those files decide what CI and releases execute.
- **Publishing stays out of reach:** the release workflow does not run on PRs, and the `pypi` environment refuses non-tag refs.

## Administrator bypass and emergency recovery

- **No bypass on `main`:** the rulesets give no bypass on `main`, nor on moving or deleting tags. Administrators can only bypass the tag *creation* restriction.
- **Emergency** (CI broken by an external outage while a security fix must ship):
  1. temporarily set the `main` ruleset enforcement to *disabled* (Settings → Rules → Rulesets), in a commit-sized window;
  2. merge the fix;
  3. re-enable the ruleset immediately;
  4. record what happened in the PR and, for security fixes, in the advisory.
- **A wrong release tag** is never moved: publish a new version (see `docs/versioning.md` and `docs/incident-response.md`).

## Periodic access review (every quarter, and after any incident)

- [ ] Collaborators and their roles (Settings → Collaborators); remove unused access.
- [ ] Deploy keys, GitHub Apps and installed integrations; remove unknown ones.
- [ ] PyPI and TestPyPI: project owners, maintainers and trusted publishers (`release.yml`, environments `pypi` and `testpypi` only).
- [ ] Rulesets and environments still match this document (`gh api repos/mhbxyz/kdenlive-mcp/rulesets`).
- [ ] Security features still enabled: Dependabot, secret scanning, push protection, private vulnerability reporting, CodeQL.

## Verification log

Verified on 2026-11-08:

- **Direct push to `main`:** rejected (`GH013: Changes must be made through a pull request; 8 of 8 required status checks are expected`), and `main` was unchanged.
- **Effective rules on `main`** (`GET /rules/branches/main`): deletion, non_fast_forward, required_linear_history, pull_request, required_status_checks.
- **Environments:** `testpypi` and `pypi` have deployment policy `v*` (tag) and no reviewers.
- **Tag immutability, exercised with `v0.1.0rc1` (#29):** `git push -f` moving the tag was rejected (`GH013 … Cannot update this protected ref`), and the tag is unchanged.
- **Tag creation by an admin:** creating `v0.1.0rc1` succeeded through the admin bypass; the push output reported the bypassed creation rule.

Not changed: repository visibility, collaborators, third-party access.
