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
