FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402
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
USER node
EXPOSE 3000
CMD ["npm", "start"]
