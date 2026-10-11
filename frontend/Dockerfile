# Frontend migration Part B: multi-stage — build the Vite app, then serve
# only its static output. This is also the shipped image:
# .github/workflows/release.yml builds it and pushes it to GHCR, and a
# release install (truenas/obsidian-release.yaml) serves exactly what it
# built, nginx.conf included.
#
# The git-pull install (truenas/custom-app-compose.yaml) is the exception:
# it bind-mounts the clone's frontend/dist over /usr/share/nginx/html,
# shadowing this stage's output, and deploy.py's `npm ci`/`npm run build`
# regenerates that dist/ in place. There this image only matters at
# container creation, and for nginx.conf, which a pull can't deliver.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
# ca-certificates: nginx.conf's TMDB image cache verifies the upstream
# certificate (proxy_ssl_verify on) rather than trusting whatever
# answers, and that needs a trust store present. It is not guaranteed in
# this base image, and a missing one is not a soft failure — nginx
# refuses to start, which takes the whole site with it. So install it
# explicitly rather than hoping.
#
# The cache directory is created here, owned by the user the workers run
# as, because a Docker named volume mounted over a path that exists in
# the image inherits that path's ownership. Created only at runtime it
# would land root-owned and every cache write would quietly fail, leaving
# a proxy that works and never caches.
RUN apk add --no-cache ca-certificates \
    && mkdir -p /var/cache/nginx/tmdb \
    && chown -R nginx:nginx /var/cache/nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

# Anything nginx answers counts: this is for "is nginx up and serving",
# which is what an update needs to know before calling a release good.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
