# macOS Distribution Boundary

Hashsphere uses the shared Python package. Run `scripts/install-unix.sh` from a
reviewed checkout; no macOS miner source lives here. CUDA is unsupported on
macOS. Apple Silicon native C builds were previously exercised, while Intel and
the current packaging HEAD still require their CI gates. Full instructions are
in `docs/13-installation-and-packaging.md`.
