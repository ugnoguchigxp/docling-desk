FROM oven/bun:1.4.2-slim AS dependencies
# OS store only: Bun 1.4.2 still has an affected embedded CA bundle.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20250419+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production

FROM oven/bun:1.4.2-slim
# OS store only: Bun 1.4.2 still has an affected embedded CA bundle.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20250419+deb13u1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=dependencies /app/node_modules/ node_modules/
COPY package.json ./
COPY src/ src/
RUN groupadd --gid 10001 knowledge && useradd --uid 10001 --gid 10001 --create-home knowledge \
    && mkdir -p /var/lib/knowledge /artifacts \
    && chown -R 10001:10001 /var/lib/knowledge /artifacts
USER 10001:10001
EXPOSE 18766
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD bun -e "fetch('http://127.0.0.1:18766/health/ready').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["bun", "src/server.ts"]
