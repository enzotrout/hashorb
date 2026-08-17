# HashOrb Prerequisites

Prepare your machine before following the [Quick Start Guide](QUICKSTART.md).

## Plain Talk

If the commands on this page work, your machine is ready to install HashOrb. You do not need Bitcoin Core for the normal CKPool Quick Start, and you do not need CUDA unless you specifically want NVIDIA GPU hashing.

For live CKPool mining you also need a **public Bitcoin receive address that you control**. HashOrb never needs a wallet seed phrase, private key, or wallet password.

## Choose Your Path

| Path | Required before starting |
| --- | --- |
| Linux CPU | Git, CPython 3.13, `uv` |
| Linux NVIDIA CUDA | Linux CPU requirements plus NVIDIA driver and CUDA toolkit |
| macOS CPU | Git, CPython 3.13, `uv` |
| Windows CPU | Git, CPython 3.13, `uv`, PowerShell |
| Docker CPU | Git and Docker Engine or Docker Desktop |

An internet connection is required to clone the repository, install dependencies, and connect to CKPool for live mining.

## Linux

### Required

Install:

- Git
- CPython 3.13
- `uv`

Use the package-management method appropriate for your Linux distribution. HashOrb's installer intentionally does not use `sudo`, install Python, install `uv`, or modify your shell profile.

Verify:

```bash
git --version
python3.13 --version
uv --version
```

If your system exposes Python 3.13 as `python` instead of `python3.13`, that is fine as long as `uv` can locate an existing CPython 3.13 interpreter.

### Optional native CPU backend

HashOrb can use its portable Python backend without a compiler. To build the optional native C backend, install your distribution's normal C compiler and CPython development/build tools.

After HashOrb is installed, `hashorb doctor` reports whether the native backend is available.

### Optional NVIDIA CUDA

For NVIDIA GPU hashing, also install and verify:

- a compatible NVIDIA driver
- the NVIDIA CUDA toolkit, including `nvcc`

Verify the host before building HashOrb's CUDA extension:

```bash
nvidia-smi
nvcc --version
```

CUDA architecture selection is hardware-specific. Do not copy an architecture value from another GPU without confirming it for your host.

## macOS

### Required

Install:

- Git
- CPython 3.13
- `uv`

If you already use Homebrew, one convenient setup is:

```bash
brew install git python@3.13 uv
```

Verify:

```bash
git --version
python3.13 --version
uv --version
```

### Optional native CPU backend

The portable Python backend does not require Xcode. If you want the optional native C backend and do not already have a compiler toolchain, install Apple's Command Line Tools:

```bash
xcode-select --install
```

HashOrb does not provide a CUDA backend on macOS.

## Windows

### Required

Use a normal non-administrator PowerShell session for HashOrb itself. Install:

- Git for Windows
- CPython 3.13
- `uv`
- PowerShell, which is already present on supported Windows systems

After installing Python, make sure the Python launcher can see 3.13. Verify:

```powershell
git --version
py -3.13 --version
uv --version
$PSVersionTable.PSVersion
```

If `py -3.13` cannot find Python, correct the Python installation before running the HashOrb installer.

HashOrb's Windows installer does not change execution policy, request administrator access, install Python or `uv`, or modify global `PATH` settings.

### Optional native or CUDA work

The portable Python backend is the required Windows path. Native MSVC builds and Windows CUDA remain optional boundaries and are not part of the fastest supported Quick Start.

## Docker

### Required

Install:

- Git
- Docker Engine on Linux, or Docker Desktop on macOS/Windows

You do **not** need to install Python 3.13 or `uv` on the host when you use only the Docker path. The image build provides the Python runtime and installs HashOrb inside the container.

Verify:

```bash
git --version
docker --version
docker run --rm hello-world
```

The current repository Dockerfile is CPU-only. It does not provide an NVIDIA CUDA image.

## Bitcoin Receive Address

The normal Quick Start connects to CKPool. Before the live-mining step, have a public Bitcoin receive address that you control available for:

```dotenv
HASHORB_BITCOIN_ADDRESS=YOUR_BITCOIN_ADDRESS
```

A receive address is public information. Never place any of the following in HashOrb configuration:

- seed phrase
- private key
- wallet password
- wallet recovery material

## Ready Check

Before moving on, you should be able to complete the verification block for your chosen platform without a command-not-found error.

Then continue to:

**[HashOrb Quick Start →](QUICKSTART.md)**

The Quick Start will clone the project, create `.env`, install HashOrb, run `hashorb doctor`, and start with a bounded five-minute mining session.