#!/usr/bin/env bash
# Load the TypeSafe API key into the environment for the Python scripts and
# the Rust kernel example. The key never appears in any file in this repo or
# in forensics.db (request headers are deliberately not recorded).
#
#     export TYPESAFE_API_KEY=...   # or keep it in a dotenv file and:
#     source env.sh ~/.creds/typesafe.env
if [ -n "$1" ] && [ -f "$1" ]; then set -a; . "$1"; set +a; fi
export TYPESAFE_API_KEY="${TYPESAFE_API_KEY:-$API_KEY}"
[ -n "$TYPESAFE_API_KEY" ] || echo "TYPESAFE_API_KEY is not set" >&2
