# BrainKB MCP server — hosted remote (streamable-http) image.
FROM python:3.12-slim

# No .pyc, unbuffered logs (better for container log streaming)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
# eMudhra/emSign CA incident (October 2026): the emSign root was distrusted, and pip
# below 26.2.2 still trusts it through its own vendored certifi, alongside the system
# store — so pip's TLS to PyPI could be intercepted. Before anything else is
# downloaded, install the fixed pip and certifi from pinned wheels, hash-checked, so
# even a tampered response is refused. Advisory:
# https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576
RUN printf '%s\n' \
      'pip==26.2.2 --hash=sha256:fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70' \
      'certifi==2026.10.19 --hash=sha256:42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b' \
      > /tmp/ca-fix.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/ca-fix.txt \
    && rm /tmp/ca-fix.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import certifi, pip; from pip._vendor import certifi as vc; \
assert tuple(map(int, pip.__version__.split('.')[:3])) >= (26, 2, 2), pip.__version__; \
assert certifi.__version__ >= '2026.10.19' and vc.__version__ >= '2026.10.19', (certifi.__version__, vc.__version__)"

COPY server.py server.json qa_registry.py named_entities_qa.py resources_qa.py resource_entity_bridge_qa.py oauth_server.py brainkb_logo.png ./

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
