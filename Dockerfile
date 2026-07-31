ARG PYTHON_VERSION=3.13.14

FROM python:${PYTHON_VERSION}-bookworm@sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779 AS builder

ENV HASHPHERE_BUILD_CUDA=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /build

COPY pyproject.toml setup.py MANIFEST.in README.md ./
COPY src ./src

RUN python -m pip wheel --wheel-dir /wheels .


FROM python:${PYTHON_VERSION}-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS runtime

ARG HASHPHERE_GID=10001
ARG HASHPHERE_UID=10001
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN groupadd --gid "${HASHPHERE_GID}" hashphere \
    && useradd --uid "${HASHPHERE_UID}" --gid hashphere --create-home hashphere
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels hashphere \
    && rm -rf /wheels

WORKDIR /app
RUN mkdir -p /app/logs && chown hashphere:hashphere /app/logs
VOLUME ["/app/logs"]
USER hashphere

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["hashsphere", "doctor", "--log-dir", "/app/logs"]
ENTRYPOINT ["hashsphere"]
CMD ["doctor", "--log-dir", "/app/logs"]
