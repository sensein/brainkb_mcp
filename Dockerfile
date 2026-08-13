# BrainKB MCP server — hosted remote (streamable-http) image.
FROM python:3.12-slim

# No .pyc, unbuffered logs (better for container log streaming)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py server.json ./

# Runtime defaults for the hosted remote. Override BRAINKB_URL at deploy time to
# point at your query_service (the container's localhost is NOT your backend).
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080 \
    BRAINKB_URL=http://localhost:8010

EXPOSE 8080

# Run as a non-root user. The upload staging directory has to be created HERE, while
# we are still root: the server runs as uid 10001 and cannot mkdir under /var/lib, so
# without this every POST /upload fails with a permission error (and `mkdir -p` inside
# the running container fails the same way). If you bind-mount a host directory over
# it, that directory must also be writable by uid 10001.
RUN useradd -m -u 10001 mcp \
    && mkdir -p /var/lib/brainkb-uploads \
    && chown -R mcp:mcp /app /var/lib/brainkb-uploads
USER mcp

# Liveness: the MCP HTTP port accepts connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import socket,os;s=socket.socket();s.settimeout(3);s.connect(('127.0.0.1',int(os.getenv('MCP_PORT','8080'))));s.close()" || exit 1

CMD ["python", "server.py"]
