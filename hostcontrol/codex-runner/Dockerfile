FROM node:22-bookworm-slim

# The host also pins the final image by digest. Update this version deliberately.
ARG CODEX_VERSION=0.169.0
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force
COPY runner.mjs /opt/hostcontrol/runner.mjs
WORKDIR /workspace
ENTRYPOINT ["node", "/opt/hostcontrol/runner.mjs"]
