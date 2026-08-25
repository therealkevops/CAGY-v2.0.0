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

# Create agy command helper inside container
RUN printf '#!/bin/bash\n\
echo "========================================================="\n\
echo "     Antigravity (AGY) Containerized Environment         "\n\
echo "========================================================="\n\
echo "Workspace Path : /workspace"\n\
echo "Node.js Version: $(node -v)"\n\
echo "Python Version : $(python3 --version)"\n\
echo "Git Version    : $(git --version)"\n\
echo "Ripgrep Version: $(rg --version | head -n 1)"\n\
echo "SSL Cert Bundle: $SSL_CERT_FILE"\n\
echo "========================================================="\n\
if [ "$#" -gt 0 ]; then\n\
  exec "$@"\n\
fi\n' > /usr/local/bin/agy && chmod +x /usr/local/bin/agy

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
