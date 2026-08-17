# macOS

## Plain Talk

macOS runs the shared HashOrb package with CPU backends. CUDA is not available on macOS.

For the shortest path from checkout to a bounded live mining run, use the [Quick Start Guide](../../docs/QUICKSTART.md#macos).

The user-local installer is:

```bash
./scripts/install-unix.sh install
```

Apple Silicon native C has been exercised, while broader platform-specific validation remains governed by the current CI and packaging gates. Detailed boundaries are documented in [Installation and Packaging](../../docs/13-installation-and-packaging.md).