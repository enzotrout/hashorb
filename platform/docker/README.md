# Docker Distribution Boundary

The repository-root CPU Dockerfile consumes the shared package and contains no
miner copy. It builds a CPU wheel in one stage and runs the installed console
command as a non-root user in the final stage. NVIDIA-container packaging is
deferred. Full instructions are in `docs/13-installation-and-packaging.md`.
