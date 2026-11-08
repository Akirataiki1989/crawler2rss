#!/bin/sh
set -eu
cd /volume1/docker/crawler2rss
exec /usr/bin/flock -n /volume1/docker/crawler2rss/run.lock /usr/local/bin/docker compose run --rm --no-deps crawler
