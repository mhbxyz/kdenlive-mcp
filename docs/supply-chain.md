# Dependency and CI supply chain

## Dependency policies

| Kind | Where | Policy |
|---|---|---|
| Runtime | `pyproject.toml` `dependencies` | Major-bounded ranges only (`mcp>=2.3,<3`, `pydantic>=2.11,<3`). No exact pins: users must be able to receive security fixes without a new release of this package. |
| Development | `[dependency-groups] dev` + `uv.lock` | `uv.lock` pins everything for reproducible CI and development. It does not constrain users. |
| Build | `[build-system] requires` | `hatchling>=1.27`. The build runs in an isolated environment (`uv build`). |
| CI tools | `.github/workflows/*.yml` | `uv` pinned (`UV_VERSION`), `pip-audit` pinned (`pip-audit==2.10.1`), every action pinned to a **commit SHA** with the release tag in a comment. |
| System programs | not installed by the package | Kdenlive, MLT, FFmpeg come from the user's distribution (see `docs/security.md`, executable trust). |

## Automated checks

| Check | How | When |
|---|---|---|
| Known vulnerabilities | `pip-audit --strict` on the locked runtime dependencies (`uv export --no-dev`) | every PR, every push to `main`, and **weekly** (Monday cron), to catch new advisories |
| Licenses | `scripts/check_licenses.py`: runtime licenses must be permissive or file-level copyleft (MIT, BSD, Apache, ISC, PSF, MPL-2.0…); GPL or unknown fails | every CI run |
| Dependabot alerts | GitHub dependency graph | continuous |
| Dependabot security updates | automatic PRs for vulnerable dependencies | continuous |
| Dependabot version updates | `.github/dependabot.yml`: weekly grouped PRs for `uv` and `github-actions` (SHA pins are updated too) | weekly |
| Secret scanning + push protection | GitHub, on every push | continuous |
| Code scanning | CodeQL default setup (Python, Actions) | on push and PR |

Feature availability on this repository (public, personal account, checked 2026-11-08):

- **Available and enabled:** Dependabot alerts and security updates, secret scanning, push protection, CodeQL default setup.
- **Unavailable:** custom (non-provider) secret patterns and secret validity checks require GitHub Advanced Security. As a fallback, push protection covers known providers, and CI tests never print environments.

## Workflow hardening

- **Permissions:** workflows declare `permissions: contents: read`. Release jobs get `id-token: write` only in their own job (#29).
- **Credentials:** `actions/checkout` runs with `persist-credentials: false`.
- **No untrusted code in privileged contexts:** no `pull_request_target` and no secrets in PR workflows. The release workflow runs only on tags pushed by maintainers, in protected environments (#31).
- **No long-lived tokens:** publishing uses PyPI Trusted Publishing (OIDC), not tokens.

## Triage and remediation

1. **Runtime vulnerability** (Dependabot alert, `pip-audit` failure):
   - check whether the vulnerable code path is reachable from this server;
   - merge the Dependabot PR, or raise the lower bound in `pyproject.toml`;
   - release a patch version. Target: within 7 days for high or critical, the next release otherwise.
2. **Development or CI-only vulnerability:** update the lock or pin; no release needed.
3. **Malicious or compromised dependency or action:**
   - pin to the last known-good version or SHA immediately and stop releases;
   - follow the incident process in `SECURITY.md` (#30): yank affected releases if they shipped it, and notify users through a GitHub Security Advisory.
4. **False positive or unreachable vulnerability:**
   - add `--ignore-vuln <ID>` to the audit step, together with an entry in the table below giving the reason, the owner and a review date;
   - remove it when fixed upstream.

### Recorded exceptions

| Advisory | Package | Reason | Owner | Review by |
|---|---|---|---|---|
| — | — | none | — | — |

## Licenses

The package is MIT. Runtime dependencies are only *depended upon*, never bundled, so permissive
and file-level copyleft (MPL-2.0) licenses are compatible. A new dependency with another
license fails `check_licenses.py`. It needs a maintainer decision recorded in `EXCEPTIONS`
inside the script, with the reason.
