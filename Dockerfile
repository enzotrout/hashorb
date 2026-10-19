ARG PYTHON_VERSION=3.13.14

FROM python:${PYTHON_VERSION}-bookworm@sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779 AS builder

ENV HASHORB_BUILD_CUDA=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /build

COPY pyproject.toml setup.py MANIFEST.in README.md ./
COPY src ./src

RUN python -m pip wheel --wheel-dir /wheels .


FROM python:${PYTHON_VERSION}-slim-trixie@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6 AS runtime

ARG HASHORB_GID=10001
ARG HASHORB_UID=10001
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid "${HASHORB_GID}" hashorb \
    && useradd --uid "${HASHORB_UID}" --gid hashorb --create-home hashorb
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels hashorb \
    && rm -rf /wheels

WORKDIR /app
RUN mkdir -p /app/logs && chown hashorb:hashorb /app/logs
VOLUME ["/app/logs"]
USER hashorb

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["hashorb", "doctor", "--log-dir", "/app/logs"]
ENTRYPOINT ["hashorb"]
CMD ["doctor", "--log-dir", "/app/logs"]
