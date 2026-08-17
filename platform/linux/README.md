# Linux

## Plain Talk

Linux can run the portable CPU miner, optional native CPU backends, and the explicitly built NVIDIA CUDA backend when the required toolchain is present.

For the shortest path from checkout to a bounded live mining run, use the [Quick Start Guide](../../docs/QUICKSTART.md#linux).

The user-local CPU installer is:

```bash
./scripts/install-unix.sh install
```

CUDA remains a separate explicit local source build and is not part of the portable CPU wheel. Detailed validation and packaging boundaries are documented in [Installation and Packaging](../../docs/13-installation-and-packaging.md).