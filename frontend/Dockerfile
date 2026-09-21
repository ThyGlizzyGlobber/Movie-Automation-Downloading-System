# Frontend migration Part B: multi-stage — build the Vite app, then serve
# only its static output. This image only matters at initial container
# creation: truenas/custom-app-compose.yaml bind-mounts frontend/dist over
# /usr/share/nginx/html at runtime, shadowing whatever this build stage
# produced, so a NAS update never needs this image rebuilt — only
# deploy.py's own `npm ci`/`npm run build` (run against that same host
# path) regenerating dist/ in place. Local Compose (no such bind mount)
# genuinely serves this stage's own build output instead.
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
