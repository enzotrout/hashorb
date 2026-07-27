# Hashphere

Hashphere is an experimental Python Bitcoin mining project. Its current live
operation is limited to a Stratum subscription and authorization handshake; it
does not mine or submit shares.

## Configure the environment

Copy the example configuration and edit the new `.env` file:

```bash
cp .env.example .env
```

Set `HASHPHERE_BITCOIN_ADDRESS` to a public Bitcoin receive address. The
default endpoint is Solo CKPool at `stratum.ckpool.org:3333`, the default
password is CKPool's conventional `x`, and `HASHPHERE_WORKER_NAME=auto`
derives a sanitized worker name from the hostname. No seed phrase, private key,
or wallet password is needed or should be placed in `.env`.

## Run a live Stratum handshake

Live network access is opt-in. Run exactly:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 uv run python -m hashphere stratum-handshake
```

The command loads `.env` through `Settings.from_env()`, connects to the
configured host and port, subscribes, authorizes, prints a sanitized summary,
and closes the connection. The summary contains:

- Stratum host and port
- a partially masked Stratum username
- `extra_nonce_1`
- `extra_nonce_2_size`
- the final handshake state (`AUTHORIZED` on success)

It never prints the configured Stratum password, complete payout address, or
complete Stratum username.

A successful handshake exits with status `0`. Invalid configuration, a missing
opt-in flag, connection failure, malformed protocol data, a pool error, or
authorization rejection produces a concise error on standard error and exits
nonzero. The command has no mining, share submission, reconnect, thread, or
async behavior.

The live command is the manual integration check. It is not invoked by the
default pytest suite and cannot contact CKPool unless
`HASHPHERE_ENABLE_LIVE_STRATUM=1` is explicitly set.

## Observe live Stratum notifications

To complete a handshake and wait for both supported mining notification types,
run exactly:

```bash
HASHPHERE_ENABLE_LIVE_STRATUM=1 uv run python -m hashphere stratum-observe
```

The observer consumes parsed notifications through `StratumClient` until it
has seen at least one `mining.set_difficulty` and one `mining.notify`, in either
order. Notifications queued during the handshake are included. It then prints
a sanitized summary and closes the connection. For example:

```text
Stratum notification observation succeeded.
Endpoint: stratum.ckpool.org:3333
Username: bc1q…ook1
Arrival order: mining.set_difficulty -> mining.notify
Difficulty: 500000
Job ID: 1a2b3c
Previous block hash: 00000000…89abcdef
Coinbase part 1 hex characters: 184
Coinbase part 2 hex characters: 196
Merkle branch count: 12
Version: 20000000
Network bits: 170fffff
Network time: 68764abc
Clean jobs: true
Extra nonce 1: 08000002
Extra nonce 2 size: 4
State: AUTHORIZED
```

The difficulty and job are reported as independent observations. The command
does not claim that the observed difficulty applies to the displayed job, and
it does not combine them into a mining-job model. It also does not mine, hash,
submit shares, reconnect, poll, or start threads. Complete payout addresses,
usernames, coinbase parts, passwords, and raw JSON are never displayed.

The existing transport read timeout bounds how long the command waits for each
incoming message. Missing opt-in or invalid configuration exits nonzero;
connection, authorization, timeout, malformed-message, unsupported-
notification, and other protocol failures emit a generic sanitized error and
also exit nonzero.
