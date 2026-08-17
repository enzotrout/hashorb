# Docker

## Plain Talk

The repository-root Dockerfile builds the portable CPU HashOrb package in one stage and runs the installed `hashorb` command as a non-root user in the final image.

For the shortest path from image build to a bounded live mining run, use the [Quick Start Guide](../../docs/QUICKSTART.md#docker).

Build the CPU image with:

```bash
docker build -t hashorb:cpu .
```

The current repository does not publish or claim support for a CUDA Docker image. Detailed container, volume, and hardening guidance is in [Installation and Packaging](../../docs/13-installation-and-packaging.md).