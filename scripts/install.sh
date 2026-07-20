#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env"
venv_dir="$project_dir/.venv"
postgres_data_dir="$project_dir/.postgres"
postgres_socket_dir="$project_dir/.postgres-socket"
no_systemd=false
initdb_command=""
pg_ctl_command=""
psql_command=""
createdb_command=""

case "${1:-}" in
	"") ;;
	--no-systemd) no_systemd=true ;;
	*) echo "Usage: $0 [--no-systemd]" >&2; exit 2 ;;
esac

fail() {
	echo "Error: $*" >&2
	exit 1
}

require_local_postgres_commands() {
	local postgres_bin_dir
	command -v pg_config >/dev/null 2>&1 || fail "pg_config is required for --no-systemd"
	postgres_bin_dir="$(pg_config --bindir)"
	initdb_command="$postgres_bin_dir/initdb"
	pg_ctl_command="$postgres_bin_dir/pg_ctl"
	psql_command="$postgres_bin_dir/psql"
	createdb_command="$postgres_bin_dir/createdb"
	[[ -x "$initdb_command" ]] || fail "initdb is required for --no-systemd"
	[[ -x "$pg_ctl_command" ]] || fail "pg_ctl is required for --no-systemd"
	[[ -x "$psql_command" ]] || fail "psql is required for --no-systemd"
	[[ -x "$createdb_command" ]] || fail "createdb is required for --no-systemd"
}

setup_local_postgres() {
	[[ "$database_host" == "127.0.0.1" || "$database_host" == "localhost" ]] \
		|| fail "--no-systemd requires a local PostgreSQL host"

	if [[ ! -f "$postgres_data_dir/PG_VERSION" ]]; then
		local password_file
		password_file="$(mktemp)"
		chmod 600 "$password_file"
		printf '%s' "$database_password" > "$password_file"
		"$initdb_command" --pgdata="$postgres_data_dir" --username="$database_user" \
			--pwfile="$password_file" --auth-host=scram-sha-256 --auth-local=peer
		rm -f "$password_file"
	fi

	if ! "$pg_ctl_command" --pgdata="$postgres_data_dir" status >/dev/null 2>&1; then
		mkdir -p "$postgres_socket_dir"
		chmod 700 "$postgres_socket_dir"
		"$pg_ctl_command" --pgdata="$postgres_data_dir" --wait start \
			--options="-p $database_port -k $postgres_socket_dir"
	fi

	if ! PGPASSWORD="$database_password" "$psql_command" --host="$database_host" --port="$database_port" \
		--username="$database_user" --dbname=postgres --tuples-only --no-align \
		--command="SELECT 1 FROM pg_database WHERE datname = '$database_name'" | grep -qx '1'; then
		PGPASSWORD="$database_password" "$createdb_command" --host="$database_host" --port="$database_port" \
			--username="$database_user" "$database_name"
	fi
}

command -v python3.13 >/dev/null 2>&1 || fail "Python 3.13 is required"
if "$no_systemd"; then
	require_local_postgres_commands
fi

read -r -p "Telegram bot token: " bot_token
[[ -n "$bot_token" ]] || fail "Telegram bot token cannot be empty"

read -r -p "Owner Telegram ID: " owner_id
[[ "$owner_id" =~ ^[1-9][0-9]*$ ]] || fail "Owner ID must be a positive integer"

read -r -p "PostgreSQL database [lolzup]: " database_name
database_name="${database_name:-lolzup}"
[[ "$database_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "PostgreSQL database must be a valid identifier"
read -r -p "PostgreSQL user [bot]: " database_user
database_user="${database_user:-bot}"
[[ "$database_user" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "PostgreSQL user must be a valid identifier"
read -r -s -p "PostgreSQL password [bot]: " database_password
echo
database_password="${database_password:-bot}"
read -r -p "PostgreSQL host [127.0.0.1]: " database_host
database_host="${database_host:-127.0.0.1}"
read -r -p "PostgreSQL port [5432]: " database_port
database_port="${database_port:-5432}"
[[ "$database_port" =~ ^[1-9][0-9]*$ ]] || fail "PostgreSQL port must be a positive integer"

if "$no_systemd"; then
	setup_local_postgres
fi

database_url="$({
	export DATABASE_USER="$database_user"
	export DATABASE_PASSWORD="$database_password"
	export DATABASE_HOST="$database_host"
	export DATABASE_PORT="$database_port"
	export DATABASE_NAME="$database_name"
	python3.13 -c 'import os; from urllib.parse import quote; print("postgresql+asyncpg://%s:%s@%s:%s/%s" % (quote(os.environ["DATABASE_USER"], safe=""), quote(os.environ["DATABASE_PASSWORD"], safe=""), os.environ["DATABASE_HOST"], os.environ["DATABASE_PORT"], quote(os.environ["DATABASE_NAME"], safe="")))'
})"
umask 077
printf 'BOT_TOKEN=%q\nOWNER_ID=%q\nDATABASE_URL=%q\nLOG_LEVEL=INFO\nSCHEDULER_POLL_SECONDS=60\n' \
	"$bot_token" "$owner_id" "$database_url" > "$env_file"

python3.13 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install -e "$project_dir"

cd "$project_dir"
set -a
source "$env_file"
set +a
"$venv_dir/bin/alembic" upgrade head

echo
echo "Installation completed. Start the bot with:"
if "$no_systemd"; then
	echo "  ./scripts/run.sh --no-systemd"
else
	echo "  ./scripts/run.sh"
fi
