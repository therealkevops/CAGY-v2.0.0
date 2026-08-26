FROM node:22-bookworm-slim

USER root

# Prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install core development tools, Python, Git, Ripgrep, Supervisor, Docker CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    ripgrep \
    jq \
    unzip \
    procps \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    supervisor \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies for Hermes WebUI
RUN pip3 install --no-cache-dir --break-system-packages pyyaml cryptography psutil

# Prepare persistent configuration and workspace directories
RUN mkdir -p /opt/data /workspace /root/.gemini /root/.config

# Install official Google Antigravity (AGY) Linux CLI with proxy certificate handling
RUN echo "insecure" > /root/.curlrc \
    && curl -fsSL -k https://antigravity.google/cli/install.sh | bash -s -- -d /usr/local/bin \
    && rm -f /root/.curlrc \
    && chmod +x /usr/local/bin/agy

# Add supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Environment variables
ENV WORKSPACE_DIR=/workspace
ENV DATA_DIR=/opt/data
ENV SSL_CERT_FILE=/opt/data/system_certs.pem
ENV REQUESTS_CA_BUNDLE=/opt/data/system_certs.pem
ENV NODE_EXTRA_CA_CERTS=/opt/data/system_certs.pem
ENV CURL_CA_BUNDLE=/opt/data/system_certs.pem

WORKDIR /workspace

EXPOSE 8989

# Start supervisor in foreground by default
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
