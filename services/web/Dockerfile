# Two stages (L2): the build has every package, ESLint's included; what runs has only the production ones and the
# built app, so no development tool ships.
FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Install exactly what the lockfile pins (`make lock` writes it), else resolve the ranges.
COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY . .
# Brand marks for the sources (scripts/brand-icons.mjs): fetched into public/brand as the image builds, so the app serves
# them itself. It never fails: offline, every source shows a neutral glyph instead.
RUN node scripts/brand-icons.mjs || true
RUN npm run build

FROM node:25-alpine@sha256:bdf2cca6fe3dabd014ea60163eca3f0f7015fbd5c7ee1b0e9ccb4ced6eb02ef4
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --omit=dev --no-audit --no-fund; else npm install --omit=dev --no-audit --no-fund; fi
COPY --from=build /app/next.config.mjs ./
COPY --from=build /app/public ./public
COPY --from=build /app/.next ./.next
USER node
EXPOSE 3000
CMD ["npm", "start"]
