FROM ghcr.io/astral-sh/uv:python3.12-bookworm

# Create non-root user with UID/GID commonly used by VS Code (1000:1000)
RUN useradd -ms /bin/bash -u 1000 vscode \
    && apt-get update && apt-get install -y sudo \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER vscode
WORKDIR /workspaces