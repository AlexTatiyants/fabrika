# Python from the version the lint job pins, plus the two other toolchains the
# checks need: node, because the console job parses every console script and
# because the tests that execute JavaScript skip without it, and git, because
# the suite makes commits in temporary repositories.
#
# No COPY of the source: the worktree arrives as a mount at /work, and anything
# baked in here would be shadowed by it. Dependencies are installed by setup,
# which has the network.
FROM python:3.13-slim-bookworm

ARG NODE_VERSION=22.11.0
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git curl ca-certificates xz-utils; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_arch=x64 ;; \
      arm64) node_arch=arm64 ;; \
      *) echo "no node tarball for $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/node.tar.xz \
      "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner; \
    rm /tmp/node.tar.xz; \
    rm -rf /var/lib/apt/lists/*; \
    python --version; node --version; git --version

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work
