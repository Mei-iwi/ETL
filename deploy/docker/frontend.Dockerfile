# syntax=docker/dockerfile:1
ARG NODE_IMAGE=node:24-bookworm-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /web
COPY frontend/package.json ./package.json
COPY deploy/docker/frontend.package-lock.json ./package-lock.json
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/index.html frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json frontend/vite.config.ts frontend/devProxyBoundary.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM ${NODE_IMAGE} AS api-docs
WORKDIR /api-docs
COPY deploy/docker/api-docs/package.json deploy/docker/api-docs/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY deploy/docker/api-docs/index.html deploy/docker/api-docs/swagger-init.js ./
RUN mkdir /output && cp index.html swagger-init.js /output/ \
    && cp node_modules/swagger-ui-dist/swagger-ui.css node_modules/swagger-ui-dist/swagger-ui-bundle.js /output/ \
    && cp node_modules/swagger-ui-dist/LICENSE /output/swagger-ui-LICENSE.txt \
    && cp node_modules/swagger-ui-dist/swagger-ui-bundle.js.LICENSE.txt /output/

FROM nginxinc/nginx-unprivileged:1.28-alpine AS runtime
COPY deploy/docker/nginx.conf /etc/nginx/nginx.conf
COPY deploy/docker/nginx.auth.template /opt/etl/nginx.auth.template
COPY --chmod=0555 deploy/docker/auth-entrypoint.sh /opt/etl/auth-entrypoint.sh
COPY --chmod=0555 deploy/docker/web-health.sh /opt/etl/web-health.sh
COPY --from=build /web/dist /usr/share/nginx/html
COPY --from=api-docs /output /usr/share/nginx/html/api-docs
USER root
RUN sed -i 's/\r$//' /opt/etl/auth-entrypoint.sh /opt/etl/web-health.sh \
    && chmod 0555 /opt/etl/auth-entrypoint.sh /opt/etl/web-health.sh
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 CMD ["/opt/etl/web-health.sh"]
ENTRYPOINT ["/opt/etl/auth-entrypoint.sh"]
