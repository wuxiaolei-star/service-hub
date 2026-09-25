# Build the SPA with the pinned Node toolchain.
# Requires BuildKit for the RUN --mount cache mount below (docker 23+ default).
FROM node:22-alpine AS build

# Optional build-time registry override, empty for npm's default.
ARG NPM_REGISTRY=""

WORKDIR /app
COPY package.json package-lock.json ./
# The npm download cache persists across builds in a cache mount.
RUN --mount=type=cache,target=/root/.npm npm ci ${NPM_REGISTRY:+--registry "$NPM_REGISTRY"}
COPY . .
RUN npm run build

# Ship only the static bundle behind a minimal Nginx.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/web-health || exit 1
