#!/usr/bin/env sh
# Start the stack plus a Cloudflare quick tunnel and print its public link.
# DJANGO_ALLOWED_HOSTS must include .trycloudflare.com, or every request over
# the link is a 400. DJANGO_CSRF_TRUSTED_ORIGINS must include
# https://*.trycloudflare.com, or every sign-in over the link is a 403.
# Stop sharing with:
#   docker compose --profile tunnel stop tunnel
set -e
cd "$(dirname "$0")/.."

docker compose --profile tunnel up -d --build tunnel

echo "Waiting for the tunnel link..."
i=0
while [ $i -lt 30 ]; do
    url=$(docker compose logs tunnel 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
    if [ -n "$url" ]; then
        echo "Share this link: $url"
        exit 0
    fi
    i=$((i + 1))
    sleep 2
done
echo "No link yet -- check: docker compose logs tunnel" >&2
exit 1
