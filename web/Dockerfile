FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Rewrites are resolved at build time: /api/* -> the API container.
ARG API_URL=http://api:8100
ENV API_URL=$API_URL NEXT_TELEMETRY_DISABLED=1
RUN npx next build

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web/package.json /web/next.config.mjs ./
COPY --from=build /web/node_modules ./node_modules
COPY --from=build /web/.next ./.next
USER node
CMD ["npx", "next", "start", "-p", "3100"]
