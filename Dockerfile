# syntax=docker/dockerfile:1.7

# Self-contained aipager workstation: python + node + claude code +
# dtach + aipager. Designed for cloud / headless deployments where
# users don't want to install Python or Node on the host. Mount
# ~/.claude and a config volume; see the README.

# ----- builder: produce the aipager wheel from source -----
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md LICENSE ./
COPY aipager ./aipager
RUN python -m build --wheel --outdir /wheels .

# ----- runtime -----
FROM python:3.12-slim

# OS deps:
#   - tini reaps zombies and forwards SIGTERM to aipager so its
#     graceful-shutdown handler (registry.save, hook_receiver.stop,
#     bot.stop) runs on `docker stop`.
#   - curl + ca-certificates are needed only to install Node.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tini \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 22 LTS + claude code. Strip the npm cache afterwards so it
# doesn't bloat the image (saves ~50 MB).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && rm -rf /root/.npm

# aipager wheel. The package's `dtach-bin` dep brings in the dtach
# binary for the container's arch (linux-amd64 / linux-aarch64),
# so no separate apt install is needed.
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/aipager-*.whl \
    && rm /tmp/aipager-*.whl \
    && pip cache purge

# Non-root runtime user (UID 1000 matches the typical host user, so
# bind-mounted ~/.claude permissions line up out of the box).
RUN useradd -m -u 1000 -s /bin/bash aipager
USER aipager
WORKDIR /home/aipager

# Mount points the user typically wires up:
#   /home/aipager/.claude         — claude credentials + sessions
#   /home/aipager/.config/aipager — bot token, chat id, settings
# A project dir at /workspace is conventional but not declared as a
# named volume — the user mounts whichever directories they want
# claude to touch.
VOLUME ["/home/aipager/.claude", "/home/aipager/.config/aipager"]

ENTRYPOINT ["/usr/bin/tini", "--", "aipager"]
CMD ["start"]
