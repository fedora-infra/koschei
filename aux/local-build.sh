#!/bin/bash
set -e
cd "$(git rev-parse --show-toplevel)"
MOCK_CONFIG="${MOCK_CONFIG:-default}"
rm -rf build
mkdir -p build/tito/
tito build --test --srpm -o build/tito/
mock -r "$MOCK_CONFIG" -n build/tito/koschei-*.src.rpm --resultdir build
