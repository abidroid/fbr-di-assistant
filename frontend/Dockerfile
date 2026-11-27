# Web image: builds the React app, then serves it with Caddy.
# Started by docker-compose.prod.yml (the Caddyfile is mounted from the repo root).

# --- Stage 1: build the static files with Node ---------------------------------
FROM node:24-slim AS build
WORKDIR /web

# Dependencies first, in their own cached layer; `npm ci` installs exactly
# what package-lock.json says.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# The browser calls the API on the same site under /api (Caddy forwards it),
# so no host name is baked in and no CORS preflight is needed.
ENV VITE_API_URL=/api
RUN npm run build

# --- Stage 2: only the built files + Caddy -------------------------------------
# Node and node_modules (hundreds of MB) stay behind in stage 1.
FROM caddy:2.10-alpine
COPY --from=build /web/dist /srv
