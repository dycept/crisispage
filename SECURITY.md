# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately** using GitHub's
["Report a vulnerability"](https://github.com/dycept/crisispage/security/advisories/new)
form (Security tab of this repository). Do not open a public issue for security problems.

Include what you found, how to reproduce it, and which version or commit is affected.
You can expect an acknowledgement within a few days. Please give us reasonable time to
fix the problem before you publish details.

## What is in scope

- `crisis.py`: escaping/XSS, link handling, path handling, command execution, build output
- `nginx.conf`, `Dockerfile`, `docker-compose.yml`: missing or weak hardening
- The generated site: anything that makes it load scripts or external resources

The design goal is a very small attack surface: no login, no scripts, no database, no
web admin, no shell. Reports that show this goal being violated are especially welcome.

## Out of scope

Your own deployment: server hardening, SSH keys, hosting accounts, DNS. See the
"Security when publishing" section of the [README](README.md) for recommendations.
