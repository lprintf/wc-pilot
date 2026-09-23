FROM node:22-alpine

ARG MIRROR=official
ARG CHINA_NPM_URL=""
ARG CUSTOM_NPM_URL=""

WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN case "$MIRROR" in \
      official) npm_url="https://registry.npmjs.org/" ;; \
      tencent)  npm_url="https://mirrors.cloud.tencent.com/npm/" ;; \
      aliyun)   npm_url="https://registry.npmmirror.com" ;; \
      china)    npm_url="${CHINA_NPM_URL:?CHINA_NPM_URL is required}" ;; \
      custom)   npm_url="${CUSTOM_NPM_URL:?CUSTOM_NPM_URL is required}" ;; \
      *) echo "Unsupported MIRROR: $MIRROR" >&2; exit 64 ;; \
    esac \
    && pnpm install --frozen-lockfile --registry "$npm_url"
COPY . .
CMD ["sh", "-c", "pnpm build && find /out -mindepth 1 -maxdepth 1 -exec rm -rf {} + && cp -r dist/. /out/"]
