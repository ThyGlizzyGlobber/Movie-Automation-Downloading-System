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
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
