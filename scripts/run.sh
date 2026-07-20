#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env"
venv_python="$project_dir/.venv/bin/python"

[[ -f "$env_file" ]] || {
	echo "Error: .env not found. Run ./scripts/install.sh first." >&2
	exit 1
}
[[ -x "$venv_python" ]] || {
	echo "Error: virtual environment not found. Run ./scripts/install.sh first." >&2
	exit 1
}

cd "$project_dir"
set -a
source "$env_file"
set +a
exec "$venv_python" -m lolzup.main
