# Security audit and threat model

Audit date: 2026-07-31  
Baseline commit: `e2ba7b4`  
Milestone status: remediation and final hardware validation in progress

## What

This audit covers Hashsphere's Python command boundaries, Bitcoin Core RPC,
template and consensus parsing, block construction, mining lifecycle, native C,
CUDA, event logs, packages, dependencies, GitHub Actions, Docker CPU image, and
operator documentation. It treats submission-capable mainnet operation as
blocked until every Critical and High finding is closed.

## Why

The miner consumes untrusted network-shaped data, handles node credentials,
executes native and GPU code, and contains an intentionally dangerous
`submitblock` authority. A correctness test alone does not prove that these
capabilities remain private, bounded, or unreachable from safer commands.

## Plain talk

Assume the node, configuration, build inputs, and runtime results can all be
wrong. Keep secrets local, keep every parser finite, independently check every
winner, and make the send button exist only in the command that explicitly
needs it.

## Severity policy

- Critical: immediate compromise or unsafe mainnet action is practical; must be
  fixed before this milestone completes.
- High: a realistic path can expose a protected asset or cross a mining
  authority boundary; must be fixed before completion.
- Medium: meaningful impact needs additional access or conditions; fix when
  practical or document a concrete mitigation.
- Low: defense-in-depth, maintainability, or limited-impact weakness.
- Informational: useful assurance or limitation without a discovered defect.

No unresolved Critical or High finding is acceptable for the next controlled
submission-capable gate.

## Protected assets

- Bitcoin RPC credentials and authentication cookies
- payout destination and exact payout script
- templates, transactions, candidate blocks, nonces, targets, and hashes
- proposal and submission authority
- Bitcoin Core node integrity and chain selection
- sanitized event logs and their availability
- source, wheel, sdist, container, SBOM, and release inputs
- native C and CUDA memory boundaries and returned results
- GitHub Actions tokens, workflow integrity, and dependency provenance
- operator configuration and the distinct mainnet safety opt-ins

## Threat actors

- compromised, malicious, slow, or malformed RPC endpoint
- malicious Stratum peer on the independent pool-mining path
- malicious environment, `.env`, command argument, cookie path, or log path
- local unprivileged user able to replace accessible files or symlinks
- compromised Python dependency, scanner, container base, or GitHub Action
- malicious pull request running with the workflow's token
- corrupted, buggy, or racing native/CUDA backend result
- accidental operator error, stale work, or ambiguous RPC failure

A dependency that already executes arbitrary code inside the Hashsphere process
can read that process's environment and call the operating system directly.
Process-local Python protocols are not a sandbox against that actor; locked
dependencies, vulnerability and secret scans, review, least-privilege CI, and
an isolated operator account reduce the chance and blast radius.

## Explicit exclusions

A compromised kernel, root or administrator account, Python interpreter,
firmware, device driver, hypervisor, GitHub control plane, or hostile physical
access is outside the enforceable boundary. Each can bypass file permissions,
memory isolation, network restrictions, or workflow controls and therefore can
fully compromise credentials, work, results, and submission authority. The
operator must restore a trusted host and rotate credentials after such an
event; Hashsphere cannot safely continue in place.

## Trust boundaries and data flows

1. The operator supplies finite CLI controls and environment configuration.
2. A template-only RPC client resolves one host once, requires every result to
   be loopback, authenticates, and exposes only chain, address validation, and
   template methods to `bitcoin-core-check` and `solo-hash`.
3. Strict JSON, template, transaction, coinbase, merkle, and block parsers turn
   bounded bytes into immutable construction state.
4. A selected Python, native, parallel, CUDA, or multi-CUDA backend receives an
   exact 76-byte header prefix, targets, and one half-open nonce range.
5. Python reconstructs every reported candidate and checks its digest, target,
   work identity, stop state, and template freshness.
6. `solo-hash` terminates without complete-block assembly or an RPC submission
   callback. Only doubly opted-in `solo-mine` constructs a full block, proposes
   once, refreshes once more, and submits once without retry.
7. The event sink accepts allowlisted field names and values and writes private,
   append-only, no-follow JSONL records. Raw protected material never enters an
   event.
8. Hosted read-only GitHub workflows build and scan unprivileged CPU artifacts.
   They receive no node, payout, Stratum, CUDA, or submission credentials.

## Command capability matrix

| Command | RPC reads | Compute | Complete block | Proposal | Submission |
| --- | --- | --- | --- | --- | --- |
| `bitcoin-core-check` | fixed read-only methods | no | no | absent | absent |
| `solo-hash` | fixed read-only methods | yes | no | absent | absent |
| `solo-mine` | fixed reads | yes | verified candidate only | exactly once | exactly once after proposal, freshness, and two startup opt-ins |

Submission permission is checked before RPC or backend construction and is
captured in the constructed policy; later environment changes are not read.
Transport ambiguity is terminal and never retried. Runtime expiry, signals,
RPC invalidation, and template replacement suppress a candidate before the
submission policy runs.

## Attack surfaces and mitigations

### RPC and authentication

RPC response bodies, timeouts, cookie records, authentication text, JSON
nesting, request IDs, result/error envelopes, rejection tokens, and supported
methods are bounded and validated. Cookie files must be owned by the effective
POSIX user, private, regular, and not symlinks; reads stop after the maximum
record size. Hostnames are resolved once and every returned IPv4 or IPv6
address must be loopback, preventing accidental plaintext credential delivery
to a remote endpoint and avoiding a second DNS resolution at connect time.

Ordinary Bitcoin Core RPC here is HTTP Basic authentication, not internet-safe
encrypted transport. Hashsphere deliberately rejects remote RPC. A remote node
requires an operator-controlled local tunnel or proxy that presents a loopback
endpoint; tunnel design and authentication are outside this milestone.

### Consensus and template parsing

The RPC body is capped at 16 MiB before JSON parsing. Transaction count, raw
body size, integers, money, size, weight, strings, dependencies, identities,
rules, mutations, compact targets, timestamps, witness commitments, and
serialized transactions all have explicit limits. Boolean-as-integer values,
duplicate JSON keys, non-finite numbers, repeated txids/raw transactions,
forward dependencies, malformed compact targets, and unknown mandatory rules
fail closed. Exact raw transaction order and bytes are retained.

Independent parser-oracle tests cover canonical compact sizes, BIP34 heights,
script numbers, txid/wtxid derivation, witness and ordinary merkle roots, odd
duplication, byte order, complete-block reparsing, size, and weight. Bitcoin
Core's isolated regtest proposal and acceptance remain the independent final
consensus oracle.

### Scheduling, lifecycle, native, and CUDA

Nonce ranges are nonempty half-open intervals within the 32-bit domain.
Sequential, orbiting-bit, CPU partitioning, and CUDA multi-device partitioning
tests prove exact parent coverage without gaps or overlap. Work identities stop
reuse after extra-nonce or timestamp progression. A backend result must match
its assigned range and is never trusted without Python reconstruction.

Native C validates exact Python types and byte lengths before copying, uses
fixed-size buffers and unsigned arithmetic, releases the GIL only after copying
inputs, checks allocation results through Python APIs, and returns owned Python
objects. CUDA validates device ordinals, evaluated launch sizes, allocations,
copy lengths, range arithmetic, launch results, and independently owned device
contexts. Host wrappers reject malformed tuples, flags, counts, ranges, and
candidates, then recompute the hash and target in Python.

### Denial of service and local files

Commands require a chunk or runtime bound; timeouts, polling, delays, worker
counts, device counts, response sizes, nesting, template memory, strings,
extra-nonce progression, and timestamp progression are finite. There is no
candidate, submission, or reconnect busy loop. Event write, flush, disk-full,
permission, close, signal, backend, and RPC failures produce sanitized terminal
categories and close owned resources.

Event files are mode `0600` on POSIX and reject symlink, non-regular, wrong-owner,
or group/world-accessible targets. Windows relies on the operator's directory
ACL. Log size is not globally rotated by the application; bounded invocations,
filesystem quotas, and operator rotation remain required.

### Supply chain, GitHub, packages, and Docker

Actions are full-SHA pinned, checkout credential persistence is disabled,
workflow permissions are read-only, hosted runners have timeouts and
concurrency cancellation, and no privileged PR trigger or artifact upload is
used. Scanner releases and downloaded Linux binaries are version and SHA-256
pinned. Secret and vulnerability reports are parsed for counts without printing
their contents.

The CPU image uses multi-architecture digest-pinned Python bases, a compiler
stage, an unprivileged numeric runtime user, an exec entry point, an offline
health command, and no embedded configuration. It supports a read-only root,
dropped capabilities, and `no-new-privileges` when `/app/logs` is supplied as a
writable volume or tmpfs. Wheel, sdist, Docker context, layers, history, and an
ephemeral SBOM are checked for private material.

## Findings

| ID | Severity | Finding | Status | Proof |
| --- | --- | --- | --- | --- |
| HS-01 | High | `solo-hash` retained a reflectable reference to the submission-capable client behind a narrow adapter | remediated in this milestone | exact read-only client has no proposal/submit methods; hostile internal dispatch test rejects `submitblock` before transport |
| HS-02 | High | explicitly configured remote or mixed-resolution RPC could receive Basic credentials over plaintext HTTP and resolution occurred again during connect | remediated in this milestone | IPv4/IPv6 loopback, mixed DNS, remote literal, and single-resolution tests |
| HS-03 | High | cookie loading followed symlinks and allocated the entire file before applying its size limit | remediated in this milestone | no-follow regular-file open, bounded read, ownership/mode, encoding, oversized, symlink, and unreadable tests |
| HS-04 | Medium | deeply nested bounded-size JSON could exhaust the recursive decoder | remediated in this milestone | pre-decode depth cap and 65-level hostile response test |
| HS-05 | Medium | event logging followed a final symlink and accepted publicly readable existing files | remediated in this milestone | private no-follow regular-file open and deterministic POSIX tests |
| HS-06 | High | GitHub Actions used mutable action tags and persisted checkout credentials | remediated in this milestone | full-SHA and checkout-credential regression tests; Actionlint and Zizmor |
| HS-07 | Medium | Docker bases used mutable tags | remediated in this milestone | multi-architecture digest pins and Docker static test |
| HS-08 | Low | workflows lacked concurrency cancellation | remediated in this milestone | Zizmor pedantic scan |
| HS-09 | Low | CUDA fallback cleanup intentionally swallowed two exceptions without making that intent inspectable | remediated in this milestone | explicit discarded cleanup error; Bandit clean |
| HS-10 | Low | Bandit treated the conventional public Stratum password placeholder as a hardcoded secret | reviewed false positive | one exact `B105` annotation with adjacent rationale; no broad allowlist |

Remediation commit hashes and final test counts are filled in after the focused
milestone commits are created.

## Residual risks and deferred work

- Arbitrary code already executing in process can bypass Python object
  capability boundaries and read environment credentials. Dependency review,
  locked resolution, scanners, and host isolation are the mitigations.
- A non-cancellable native or CUDA range may finish before a stop or runtime
  deadline is observed. Range size is operator bounded and a late candidate is
  suppressed.
- JSON and transaction parsing still consume memory proportional to the bounded
  16 MiB response. The cap is deliberate rather than constant-memory parsing.
- Application logs need operator retention or filesystem quotas; Hashsphere
  does not rotate them.
- Windows ACL semantics, Windows native extensions, macOS compiler behavior,
  and physical two-device CUDA remain CI/deterministic or deferred boundaries,
  not claims of current physical validation.
- Offline Zizmor cannot perform repository-dependent online audits. GitHub
  repository settings, CodeQL availability, native secret scanning, rulesets,
  and notification state require authenticated operator review.
- The project has no finalized public release or licensing policy. Volatile
  SBOM output is not committed or published, and SECURITY.md makes no broad
  legal safe-harbor promise.
- Scanner databases and hosted runner images remain external mutable services.
  Exact scanner executables/actions are pinned; advisory data freshness is
  intentionally updated on each scheduled run.

## Platform boundaries

- Linux: POSIX ownership/mode, symlink, signal, native sanitizer, Docker, and
  real Spark CUDA paths are validated.
- macOS: packaging CI, path/signal contracts, and deterministic native behavior
  are validated; no CUDA support is claimed.
- Windows: packaging CI and the PowerShell dry-run installer are validated;
  ACLs replace POSIX modes, Ctrl-C follows Python's supported signal set, and
  CUDA/native physical validation is not claimed.
- The Unix installer remains Linux/Darwin-only. MINGW, MSYS, Cygwin, and native
  Windows must use `scripts/install-windows.ps1`.

## GitHub operator actions

After both workflow names are visible on the default branch:

1. Open **Settings → Security → Advanced Security** and enable Dependency graph,
   Dependabot alerts, and Dependabot security updates when available.
2. Open **Security → Dependabot alerts** to review advisories; configure security
   notification routing from the repository Watch/notification controls.
3. Open **Insights → Dependency graph → SBOM → Export SBOM** for GitHub's SPDX
   inventory. Local release candidates may run
   `scripts/run-security-audit.sh artifacts` for an ephemeral CycloneDX SBOM.
4. Open **Settings → Rules → Rulesets → New branch ruleset**, target `main`, and
   recommend blocking force pushes and deletion, requiring the final
   **Packaging** and **Security** checks, and requiring the branch to be current.
   Require pull requests only if it fits the solo-maintainer workflow. Do not
   require signed commits until the maintainer's signing path is verified.
5. Review **Security** and repository settings before claiming CodeQL or
   GitHub-native secret scanning. When unavailable, the maintained substitutes
   are Bandit/pip-audit, Gitleaks, Trivy, Zizmor, and Actionlint.

These controls are recommendations only; this milestone does not change remote
rulesets or risk locking the operator out.

## Tool inventory

- Bandit 1.9.4
- pip-audit 2.10.1
- Gitleaks 8.30.1
- Actionlint 1.7.12
- Zizmor 1.28.0 (offline, pedantic)
- Trivy 0.69.3, selected and checksum-pinned as a release outside the documented
  compromised versions
- Ruff 0.15.21, mypy 2.3.0, pytest 9.1.1, uv 0.12.0
- GCC 13.3.0, CUDA toolkit 13.0, and compute-sanitizer from the installed Spark
  toolchain

## Release recommendation

No package, image, release, or SBOM is published by this milestone. The next
controlled submission-capable mainnet gate remains blocked until final native,
CUDA, container, full-suite, scanner, and clean-tree results are recorded here
with no unresolved Critical or High finding.
