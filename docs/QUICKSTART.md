# HashOrb Quick Start

Get HashOrb from a fresh checkout to a short, bounded Stratum mining run on Linux, macOS, Windows, or Docker.

## Plain Talk

You need three things: Python 3.13, `uv`, and a public Bitcoin receive address. HashOrb connects to CKPool by default, receives real mining work, hashes it, and submits qualifying shares. You never need to give HashOrb a seed phrase, private key, or wallet password.

This guide starts with a five-minute run so you can confirm the miner works before leaving it running longer.

## Before You Start

HashOrb is pre-release software. CPU mining is suitable for learning and validation, not for competing with ASIC hardware.

You need:

- Git
- CPython 3.13
- `uv`
- Internet access
- a public Bitcoin receive address that you control

For NVIDIA CUDA hashing, use Linux and follow the optional CUDA section after the basic CPU path works.

## 1. Get the Source

```bash
git clone https://github.com/enzotrout/hashorb.git
cd hashorb
```

Until the repository is public, use whatever authenticated clone URL you already use.

## 2. Create Your Configuration

Copy the example file without committing the result.

Linux/macOS:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Open `.env` and set your public payout address:

```dotenv
HASHORB_STRATUM_HOST=stratum.ckpool.org
HASHORB_STRATUM_PORT=3333
HASHORB_BITCOIN_ADDRESS=YOUR_BITCOIN_ADDRESS
HASHORB_WORKER_NAME=auto
HASHORB_STRATUM_PASSWORD=x
HASHORB_SEARCH_STRATEGY=sequential
```

Do not put a seed phrase, private key, wallet password, or other wallet secret in `.env`.

## Linux

Install the user-local tool from the reviewed checkout:

```bash
./scripts/install-unix.sh install
hashorb doctor
```

Arm live Stratum and live mining only for this shell:

```bash
export HASHORB_ENABLE_LIVE_STRATUM=1
export HASHORB_ENABLE_LIVE_MINING=1
```

Start a five-minute mining run:

```bash
hashorb stratum-mine \
  --profile auto \
  --max-runtime-seconds 300 \
  --log-file logs/events.jsonl
```

Stop earlier with Ctrl-C. A normal bounded run should finish with a controlled stop or runtime-limit result rather than an unhandled exception.

### Optional NVIDIA CUDA on Linux

First confirm the NVIDIA driver and CUDA toolkit are already installed and usable. HashOrb does not install them for you.

Build the optional CUDA extension for the architecture you intentionally choose. For example, the DGX Spark validation host uses architecture `121`:

```bash
HASHORB_BUILD_CUDA=1 \
HASHORB_CUDA_ARCH=121 \
uv sync --locked --reinstall-package hashorb
```

Then verify the device explicitly:

```bash
uv run hashorb doctor --probe-cuda-device 0
uv run hashorb compute-benchmark --backend cuda --device 0 --hash-count 1000000
```

For a live CUDA run from the project environment:

```bash
export HASHORB_ENABLE_LIVE_STRATUM=1
export HASHORB_ENABLE_LIVE_MINING=1
uv run hashorb stratum-mine \
  --profile auto \
  --max-runtime-seconds 300 \
  --log-file logs/events.jsonl
```

CUDA architecture values are hardware-specific. Do not copy `121` to another NVIDIA GPU unless that is actually the target accepted for that host.

## macOS

HashOrb uses CPU backends on macOS; CUDA is not available there.

```bash
./scripts/install-unix.sh install
hashorb doctor
```

Arm live operation for the current Terminal session:

```bash
export HASHORB_ENABLE_LIVE_STRATUM=1
export HASHORB_ENABLE_LIVE_MINING=1
```

Mine for five minutes:

```bash
hashorb stratum-mine \
  --profile auto \
  --max-runtime-seconds 300 \
  --log-file logs/events.jsonl
```

## Windows

Use a normal non-administrator PowerShell session. The installer does not change execution policy or global PATH settings.

```powershell
& .\scripts\install-windows.ps1 -Action install
hashorb doctor
```

Arm live operation for the current PowerShell session:

```powershell
$env:HASHORB_ENABLE_LIVE_STRATUM = "1"
$env:HASHORB_ENABLE_LIVE_MINING = "1"
```

Mine for five minutes:

```powershell
hashorb stratum-mine `
  --profile auto `
  --max-runtime-seconds 300 `
  --log-file logs\events.jsonl
```

The portable Python backend is the required Windows path. Native MSVC and Windows CUDA are not claimed as broadly validated yet.

## Docker

The repository Dockerfile builds the portable CPU image.

```bash
docker build -t hashorb:cpu .
```

Check the image before giving it network-enabled mining configuration:

```bash
docker run --rm hashorb:cpu
docker run --rm hashorb:cpu --help
```

Create a persistent log volume:

```bash
docker volume create hashorb-logs
```

Start a five-minute mining run:

```bash
docker run --rm \
  --env-file .env \
  -e HASHORB_ENABLE_LIVE_STRATUM=1 \
  -e HASHORB_ENABLE_LIVE_MINING=1 \
  -v hashorb-logs:/app/logs \
  hashorb:cpu stratum-mine \
  --profile auto \
  --max-runtime-seconds 300 \
  --log-file /app/logs/events.jsonl
```

The current repository Dockerfile is CPU-only. A CUDA Docker image is not currently published or claimed as supported.

## Watch the Dashboard

If you are mining directly on the host and writing `logs/events.jsonl`, open another terminal:

```bash
hashorb dashboard --log-file logs/events.jsonl
```

For a one-time snapshot:

```bash
hashorb dashboard --log-file logs/events.jsonl --once
```

## Try Another Search Strategy

Search strategy and compute backend are independent. After the basic run works, you can change only the range order in `.env`:

```dotenv
HASHORB_SEARCH_STRATEGY=orbiting-bit
```

or:

```dotenv
HASHORB_SEARCH_STRATEGY=fibonacci-bounce
```

Neither strategy claims better Bitcoin mining probability. They change deterministic search order while preserving the same underlying validity rules.

## Run Longer

Once a five-minute run behaves correctly, increase the runtime deliberately, for example:

```bash
hashorb stratum-mine --profile auto --max-runtime-seconds 3600 --log-file logs/events.jsonl
```

Omit or change lifecycle limits only after reviewing the command help and the deeper mining documentation.

## Direct Bitcoin Core Solo Mining

The quick path above uses CKPool Stratum. Direct solo mining through your own Bitcoin Core node is deliberately separate because it has stronger authority and configuration requirements.

Read [Bitcoin Core True Solo](14-bitcoin-core-true-solo.md) before enabling that path. In particular, `bitcoin-core-check`, `solo-hash`, and `solo-mine` have intentionally different capabilities and opt-ins.

## Troubleshooting

Start with:

```bash
hashorb doctor
hashorb --help
```

If you are running from the source checkout instead of the installed user-local command, prefix commands with `uv run`, for example:

```bash
uv run hashorb doctor
```

For packaging details and platform validation boundaries, see [Installation and Packaging](13-installation-and-packaging.md).