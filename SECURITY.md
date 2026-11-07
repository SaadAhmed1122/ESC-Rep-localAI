# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue. Use GitHub's private reporting
(**Security → Report a vulnerability**) on this repository. We aim to acknowledge reports
within 5 working days and to agree a disclosure timeline with you.

In scope (examples):

- Any path by which user data leaves the device: network egress, browser-side fetches,
  exports that include unexpected data.
- Bypasses of the formula sandbox, the loopback-endpoint check or the network guard.
- Weaknesses in the vault encryption, key handling or audit chain.
- XSS or injection in the UI or in exported reports.
- Prompt injection that leads to anything beyond misleading text.

## Supported versions

The latest release on `main` receives security fixes.

## Security design

See [docs/PRIVACY.md](docs/PRIVACY.md) for the threat model, the controls and their known
limitations.
