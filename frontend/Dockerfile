FROM node:22-alpine
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
CMD ["sh", "-c", "pnpm build && find /out -mindepth 1 -maxdepth 1 -exec rm -rf {} + && cp -r dist/. /out/"]
