# Security policy

## Supported versions

The project is pre-1.0. Security fixes are made on `main` and released as a new patch
version of the **latest minor release** only.

| Version | Supported |
|---|---|
| latest `0.y.z` release | ✅ |
| older releases | ❌ (upgrade to the latest release) |

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately through GitHub: go to the repository's **Security** tab, then
**Report a vulnerability**, or open
<https://github.com/mhbxyz/kdenlive-mcp/security/advisories/new>. Only the maintainers see
the report.

Please include:

- the affected version or commit;
- what an attacker can do, and the steps or a proof of concept. Use disposable projects and media, not personal files;
- whether the problem needs a malicious project file, a manipulated MCP client, or local access.

### What happens next

This is a volunteer project and no response time is guaranteed. The maintainer aims to:

1. acknowledge the report in the private advisory thread;
2. assess it and agree on severity with you;
3. prepare a fix privately, on a temporary private fork of the advisory;
4. publish a release and the advisory together, crediting you if you wish.

Please give the maintainer a reasonable time to fix the problem before you disclose it publicly.
Coordinated disclosure is the default.

## Scope

See [docs/security.md](docs/security.md) for the threat model.

- **In scope:** escaping the allowed roots, writing outside the requested destination, running unexpected commands, network access triggered by project content, data loss in projects, secrets reaching child processes or logs.
- **Out of scope:** vulnerabilities in Kdenlive, MLT or FFmpeg themselves; report those to the respective upstream projects.

Incident handling for compromised releases, credentials or dependencies is described in
[docs/incident-response.md](docs/incident-response.md).
