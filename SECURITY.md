# Security policy

## Supported versions

Hashsphere is pre-release software. Only the current `main` branch is supported
for security fixes; no published release line currently receives backports.

## Reporting a vulnerability

This repository does not yet advertise a public confidential reporting address.
Do not open a public issue for a suspected vulnerability.

If GitHub private vulnerability reporting is enabled on the repository, use
**Security → Advisories → Report a vulnerability**. Otherwise, contact a
repository administrator through an already established private GitHub channel
available to repository collaborators and ask for a confidential security
discussion. If neither channel is available, report only that a private channel
is needed—do not send vulnerability details in public.

Never include real RPC credentials, cookie contents, payout data, wallet or key
material, raw templates, transactions, headers, targets, nonces, candidate
hashes, blocks, GPU identifiers, personal paths, or production logs. Use a
minimal synthetic reproducer and redact environment-specific values.

Please include:

- the affected commit and platform category;
- the impacted command or component;
- a concise impact and precondition description;
- deterministic synthetic reproduction steps or a small test;
- whether credentials, proposal authority, submission authority, native memory,
  or consensus correctness may be affected;
- any safe mitigation already applied.

An administrator should acknowledge a complete report within five business
days. Remediation and disclosure timing depend on severity, reproducibility,
and the availability of a safe fix. Coordinate any public disclosure until a
fix and operator migration guidance are ready.

## Scope

In scope are Hashsphere source, parsers, command capability boundaries, native
and CUDA extensions, packages, the CPU container, maintained scripts, GitHub
workflows, dependency configuration, event privacy, and unsafe interactions
with Bitcoin Core or Stratum caused by Hashsphere.

Generally out of scope are social engineering, denial of service requiring
unbounded traffic to infrastructure not operated by this project, unsupported
forks or modified binaries, compromised kernels/root accounts/firmware/drivers,
Bitcoin Core or pool-server vulnerabilities independent of Hashsphere, and
reports containing only automated scanner output without a reachable impact.

Good-faith, authorized research against synthetic local fixtures and isolated
regtest environments is welcome when it avoids privacy violations, service
disruption, credential access, and any mainnet/testnet/signet submission. This
statement is coordination guidance, not a waiver of law or third-party terms
and not a broad legal safe-harbor promise.
