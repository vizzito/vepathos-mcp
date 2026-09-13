# syntax=docker/dockerfile:1.7
# Production image for vepathos-mcp (stateless Streamable HTTP MCP server).

FROM python:3.12-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="vepathos-mcp" \
      org.opencontainers.image.description="Vepathos MCP — large-scale delivery and fleet route optimization for AI agents" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/vizzito/vepathos-mcp"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080

RUN groupadd --system --gid 10001 mcp && useradd --system --uid 10001 --gid mcp --no-create-home mcp
COPY --from=build /opt/venv /opt/venv

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"

ENTRYPOINT ["vepathos-mcp"]
