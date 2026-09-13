# Security policy

## Reporting a vulnerability

Please report security issues privately to **security@vepathos.com** (or through GitHub private
vulnerability reporting on this repository). Do not open public issues for vulnerabilities.

Include the affected version or commit, reproduction steps and the impact you observed. We aim to
acknowledge reports within 3 business days.

## Scope

- This repository (`vepathos-mcp`): the MCP protocol adapter, its authentication and its handling of
  data in transit.
- The hosted service at `https://mcp.vepathos.com` and the Vepathos authorization server at
  `https://api.vepathos.com`.

Please do not run load tests or automated scanning against production, and do not access accounts you
do not own.

See `docs/security.md` for the threat model and controls.
