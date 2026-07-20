#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env"
venv_python="$project_dir/.venv/bin/python"
postgres_data_dir="$project_dir/.postgres"
postgres_socket_dir="$project_dir/.postgres-socket"
no_systemd=false
pg_ctl_command=""

case "${1:-}" in
	"") ;;
	--no-systemd) no_systemd=true ;;
	*) echo "Usage: $0 [--no-systemd]" >&2; exit 2 ;;
esac

[[ -f "$env_file" ]] || {
	echo "Error: .env not found. Run ./scripts/install.sh first." >&2
	exit 1
}
[[ -x "$venv_python" ]] || {
	echo "Error: virtual environment not found. Run ./scripts/install.sh first." >&2
	exit 1
}

if "$no_systemd"; then
	[[ -f "$postgres_data_dir/PG_VERSION" ]] || {
		echo "Error: local PostgreSQL is not initialized. Run ./scripts/install.sh --no-systemd first." >&2
		exit 1
	}
	command -v pg_config >/dev/null 2>&1 || {
		echo "Error: pg_config is required for --no-systemd." >&2
		exit 1
	}
	pg_ctl_command="$(pg_config --bindir)/pg_ctl"
	[[ -x "$pg_ctl_command" ]] || {
		echo "Error: pg_ctl is required for --no-systemd." >&2
		exit 1
	}
fi

cd "$project_dir"
set -a
source "$env_file"
set +a

if "$no_systemd" && ! "$pg_ctl_command" --pgdata="$postgres_data_dir" status >/dev/null 2>&1; then
	database_port="$($venv_python -c 'import os; from urllib.parse import urlparse; print(urlparse(os.environ["DATABASE_URL"]).port)')"
	mkdir -p "$postgres_socket_dir"
	chmod 700 "$postgres_socket_dir"
	"$pg_ctl_command" --pgdata="$postgres_data_dir" --wait start \
		--options="-p $database_port -k $postgres_socket_dir"
fi

exec "$venv_python" -m lolzup.main
