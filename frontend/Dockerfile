# syntax=docker/dockerfile:1.7
# ObserveNet web: static SPA + reverse proxy to the API (unprivileged nginx, port 8080).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Optional `extra_ca` build secret for TLS-inspecting proxies (see backend/Dockerfile).
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --ignore-scripts --no-audit --no-fund
COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts tailwind.config.js postcss.config.js ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
