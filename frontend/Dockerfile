# Stage 1: build the React app into static files (dist/).
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: the Caddy web server, with the built website in /srv.
# The Caddyfile is mounted by docker-compose.yml from caddy/Caddyfile.
FROM caddy:2
COPY --from=build /app/dist /srv
