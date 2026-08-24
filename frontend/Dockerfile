# Frontend image for Settlement Witness.
#
# The first stage builds the production bundle. The second stage serves the
# static files with nginx, so no build tooling reaches the running container.
#
# The runtime uses the unprivileged nginx image rather than the standard one.
# The standard image starts its master process as root and only drops the worker
# processes to the nginx user, so the container still has a root process in it.
# The unprivileged image runs everything as UID 101, which is why it listens on
# 8080: a process without root cannot bind a port below 1024.

FROM node:24-bookworm-slim AS build

ENV PNPM_HOME=/pnpm \
    PATH="/pnpm:${PATH}" \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0

RUN corepack enable

WORKDIR /srv/frontend

COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .
RUN pnpm run build

FROM nginxinc/nginx-unprivileged:1.31-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /srv/frontend/dist /usr/share/nginx/html

# The base image already drops to this user. Stating it here keeps the image
# unprivileged even if the base ever changes that default.
USER 101

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1:8080/ || exit 1
