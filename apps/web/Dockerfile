# syntax=docker/dockerfile:1

FROM node:24-slim AS build

RUN npm install --global pnpm@11.22.0
WORKDIR /repo

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm --filter @ai-layout/web build

# Unprivileged image: nginx listens on 8080 and runs as a non-root user.
FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1