# Security Policy

## Supported versions

| Version | Security fixes |
|---|---|
| 2.0 betas (latest beta only) | Yes |
| 1.9.x (latest release only) | Yes, until 2.0.0 is released |
| Anything older | No — please update |

Once 2.0.0 is out, only the latest 2.x release receives security fixes.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security problem.

Report it privately through [GitHub's private vulnerability reporting](https://github.com/MarcinG81/SunSynk_HA_Integration/security/advisories/new). Only the maintainer can see the report.

Please include:
- what the problem is and which version you tested;
- how to reproduce it;
- what an attacker could do with it, e.g. control an inverter or read credentials.

You'll get a reply within 48 hours. We'll agree a fix and a disclosure date with you, publish a GitHub Security Advisory with the fix, and credit you unless you'd rather not be named.

## What this integration stores

- Your Sunsynk / Deye account email and password are kept in the Home Assistant config entry, in Home Assistant's `.storage` folder. Home Assistant does not encrypt that folder, so treat your Home Assistant configuration and its backups as containing your password.
- Access tokens are kept in memory only.
- Diagnostics downloads redact your password, email, inverter serials and location.
