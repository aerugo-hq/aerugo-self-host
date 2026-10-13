FROM node:22-alpine
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Install exactly what the lockfile pins (`make lock` writes it), else resolve the ranges.
COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY . .
RUN npm run build
USER node
EXPOSE 3000
CMD ["npm", "start"]
