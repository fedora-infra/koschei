#!/bin/sh
set -e

while read state color; do
  for format in svg png; do
    curl https://img.shields.io/badge/koschei-${state}-${color}.${format} >static/images/badges/${state}.${format}
    # There seems to be some rate-limiting...
    sleep 2
  done
done <<EOF
blocked orange
untracked yellow
unresolved red
ok brightgreen
failing red
unknown lightgrey
EOF
