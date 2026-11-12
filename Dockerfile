# CPU-only distroless runtime with upstream CPython 3.13.14.
ARG PYTHON_VERSION=3.13.14
FROM python:${PYTHON_VERSION}-slim-trixie@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6 AS builder
ENV HASHORB_BUILD_CUDA=0 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml setup.py MANIFEST.in README.md ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels . \
    && python -m pip install --no-index --find-links=/wheels --target=/opt/hashorb hashorb

# Base-only distroless runtime avoids bundling Debian's older Python.
# Base manifest digest recorded from GitHub Actions on 2026-11-12.
FROM gcr.io/distroless/base-debian13:nonroot@sha256:7fe4df6a4413adf5889960d345a28b4574aea0872ea47684c7244c8d88027dca AS runtime
ENV PYTHONPATH=/opt/hashorb PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /usr/local /usr/local
COPY --from=builder /opt/hashorb /opt/hashorb
WORKDIR /app
USER 10001:10001
VOLUME ["/app/logs"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["/usr/local/bin/python", "-m", "hashorb", "doctor", "--log-dir", "/app/logs"]
ENTRYPOINT ["/usr/local/bin/python", "-m", "hashorb"]
CMD ["doctor", "--log-dir", "/app/logs"]
