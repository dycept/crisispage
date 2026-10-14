# Stage 1: build the static site from your project files (config.toml, i18n/, updates/, legal/, assets/).
# .dockerignore keeps tests, Git data and any old public/ out. Only the build result reaches the final image.
FROM python:3.13-slim AS build
WORKDIR /app
COPY . .
RUN python3 crisis.py build

# Stage 2: only the result goes into an unprivileged nginx image.
FROM nginxinc/nginx-unprivileged:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/public /usr/share/nginx/html
EXPOSE 8080
