# syntax=docker/dockerfile:1
FROM node:22.14-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# These are fixed, non-secret production choices. Investor credentials are entered after load and
# remain in sessionStorage; they are never Docker build arguments or image environment values.
RUN VITE_DATA_SOURCE=http \
    VITE_API_BASE_URL=/api/v1 \
    VITE_INVESTOR_AUTH_MODE=session \
    npm run build

FROM nginx:1.27-alpine AS runtime

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

ENV PORT=8080 \
    BACKEND_URL=http://backend:8000

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/healthz" || exit 1

CMD ["nginx", "-g", "daemon off;"]
