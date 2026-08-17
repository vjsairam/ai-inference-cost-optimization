FROM python:3.13-slim AS builder

ARG UV_VERSION=0.12.5
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /workspace

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

RUN uv sync --frozen --no-dev --no-editable


FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.source=https://github.com/vjsairam/ai-inference-cost-optimization

ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Patch base packages and drop pip: the venv is prebuilt, and pip's vendored
# dependencies would otherwise sit unused in the runtime image.
RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip

RUN groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid gateway --create-home --home-dir /home/gateway gateway \
    && mkdir -p /workspace/results/local \
    && chown -R gateway:gateway /workspace

WORKDIR /workspace

COPY --from=builder /opt/venv /opt/venv
COPY --chown=gateway:gateway pyproject.toml uv.lock ./
COPY --chown=gateway:gateway src/ src/
COPY --chown=gateway:gateway config/ config/
COPY --chown=gateway:gateway policy/ policy/
COPY --chown=gateway:gateway benchmark/ benchmark/
COPY --chown=gateway:gateway results/schema/ results/schema/

USER gateway

EXPOSE 8080

CMD ["uvicorn", "--factory", "inference_gateway.main:build_app", "--host", "0.0.0.0", "--port", "8080"]
