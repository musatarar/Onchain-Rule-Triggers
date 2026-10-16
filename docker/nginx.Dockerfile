# The config is copied in rather than bind-mounted: Docker Desktop on macOS
# can be refused read access to a project under ~/Documents.
FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
