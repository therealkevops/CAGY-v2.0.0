# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:latest AS uv-bin

FROM node:22-bookworm-slim

USER root

# Prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Copy pre-compiled uv & uvx binaries for zero-overhead Python MCP server execution
COPY --from=uv-bin /uv /uvx /usr/local/bin/

# Install core runtime tools, Python, Git, Ripgrep, Supervisor, and CA certs
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    ripgrep \
    jq \
    unzip \
    procps \
    python3 \
    python3-pip \
    python3-venv \
    supervisor \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Python dependencies for Antigravity WebUI using pre-built binary wheels
RUN pip3 install --no-cache-dir --break-system-packages pyyaml cryptography psutil \
    && rm -rf /root/.cache

# Prepare persistent configuration and workspace directories
RUN mkdir -p /opt/data /workspace /root/.gemini /root/.config /root/.agy/webui

# Install official Google Antigravity (AGY) Linux CLI with proxy certificate handling
RUN echo "insecure" > /root/.curlrc \
    && curl -fsSL -k https://antigravity.google/cli/install.sh | bash -s -- -d /usr/local/bin \
    && rm -f /root/.curlrc \
    && chmod +x /usr/local/bin/agy

# Add supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Copy application code, default skills, and knowledge into /workspace for standalone execution
COPY --chown=root:root . /workspace

# Ensure executable permissions on helper scripts
RUN chmod +x /workspace/*.sh 2>/dev/null || true

# Environment variables
ENV WORKSPACE_DIR=/workspace \
    DATA_DIR=/opt/data \
    SSL_CERT_FILE=/opt/data/system_certs.pem \
    REQUESTS_CA_BUNDLE=/opt/data/system_certs.pem \
    NODE_EXTRA_CA_CERTS=/opt/data/system_certs.pem \
    CURL_CA_BUNDLE=/opt/data/system_certs.pem

WORKDIR /workspace

EXPOSE 8989

# Start supervisor in foreground by default
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
