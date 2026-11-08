# Incident response

Owner: the repository owner (GitHub account `mhbxyz`), who is the only maintainer and the only
person able to publish releases and change security settings. If maintainership grows, this
document will name the people responsible; no other contact exists today.

## 1. Vulnerability reports (private advisories)

1. **Triage within the private advisory:**
   - reproduce with disposable files;
   - identify the affected versions;
   - decide whether the issue is in scope (see `SECURITY.md`).
2. **Severity:** use CVSS 3.1 in the advisory form. Data loss in user projects and file access outside the allowed roots are at least *high*.
3. **Fix:**
   - in the advisory's temporary private fork;
   - add a regression test in `tests/test_security.py` (or the relevant suite);
   - run the full CI on the fork;
   - request a CVE through GitHub when warranted.
4. **Release:**
   - patch release through the normal release workflow (TestPyPI, then PyPI, #29);
   - publish the advisory with the fixed version;
   - credit the reporter if they agree;
   - add a *Security* entry to `CHANGELOG.md`.
5. **Communication:** the advisory is the reference. Do not publish details before the fix is available, unless the issue is already public or actively exploited.

If the repository were private again, or private vulnerability reporting were disabled, reports
would have no private channel. In that case, re-enable reporting before anything else
(`Settings → Code security → Private vulnerability reporting`).

## 2. Leaked credentials

The project holds **no long-lived publishing credentials**: PyPI uses Trusted Publishing (OIDC),
and CI uses only the per-run `GITHUB_TOKEN`.

1. **Contain:**
   - if a maintainer's GitHub token or SSH key leaked, revoke it immediately (GitHub → Settings → Developer settings / SSH keys) and review the account's security log;
   - if a PyPI API token was ever created and leaked, revoke it on PyPI (Account settings → API tokens).
2. **Check for abuse:**
   - recent pushes, tags and releases on GitHub;
   - PyPI project history (`https://pypi.org/manage/project/mcp-kdenlive/history/`);
   - Actions runs.
3. **Rotate and tighten:**
   - review the PyPI and TestPyPI trusted publishers (repository, workflow `release.yml`, environments `pypi` and `testpypi`) and remove anything unexpected;
   - check the branch and tag rules (#31).
4. **Notify:** if a malicious artifact was published, follow section 4.

## 3. Compromised CI or dependency

1. **Stop releases:** do not push release tags; restrict the `pypi` environment if needed.
2. **Pin back** the compromised action (SHA) or dependency (lower or upper bound, `uv lock`) to a known-good version (see `docs/supply-chain.md`).
3. **Investigate:** Actions logs for unexpected network or publishing steps; artifacts built since the compromise.
4. **Released artifacts:** if any of them were built with the compromised component, treat them as malicious (section 4).

## 4. Malicious or broken PyPI artifact

1. **Yank, do not delete:** on PyPI → Manage → Releases → *Yank*. Yanked versions are no longer installed by default but stay available for audit. Deleting is irreversible and the version number can never be reused.
2. **Publish** a fixed version with a new number. Versions are immutable (`docs/versioning.md`).
3. **Advise users:** a GitHub Security Advisory naming the affected versions and how to check (`pipx list`, `kdenlive-mcp --version`), plus a changelog entry.
4. **Escalate if needed:** if the PyPI account itself is compromised, use PyPI's account recovery and contact PyPI admins through <https://pypi.org/help/>. Do not guess other contacts.
5. **MCP Registry:** mark the affected server version deprecated or deleted in the official registry through `mcp-publisher` (#33).

## 5. Unsafe project writes (data loss)

If a bug makes the server corrupt or overwrite a project:

1. **Fix it** with the highest priority and add a regression test that reproduces the corruption.
2. **Tell users how to recover:**
   - the server never edits in place without `overwrite` and the sha256 of the version read (`expected_sha256`, or `revision` for timeline and audio tools);
   - replaced files are kept as `<name>.backup-<UTC>.kdenlive`: the **first** backup (the project before the server's first edit) and the 4 most recent are kept, 0.1 backups and other files are never removed;
   - Kdenlive keeps its own `<name>_backup*` files and autosaves.
3. **Publish an advisory** if user data could be lost without the user asking for an overwrite.

## Checklist after any incident

- [ ] Root cause written in the advisory or in a post-incident note.
- [ ] Regression test added.
- [ ] Releases, PyPI history and registry entries verified.
- [ ] Documentation updated (this file, `docs/security.md`, `docs/supply-chain.md`).
