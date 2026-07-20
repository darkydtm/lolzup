#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env"
venv_dir="$project_dir/.venv"
no_systemd=false

case "${1:-}" in
	"") ;;
	--no-systemd) no_systemd=true ;;
	*) echo "Usage: $0 [--no-systemd]" >&2; exit 2 ;;
esac

fail() {
	echo "Error: $*" >&2
	exit 1
}

command -v python3.13 >/dev/null 2>&1 || fail "Python 3.13 is required"

read -r -p "Telegram bot token: " bot_token
[[ -n "$bot_token" ]] || fail "Telegram bot token cannot be empty"

read -r -p "Owner Telegram ID: " owner_id
[[ "$owner_id" =~ ^[1-9][0-9]*$ ]] || fail "Owner ID must be a positive integer"

read -r -p "PostgreSQL database [lolzup]: " database_name
database_name="${database_name:-lolzup}"
read -r -p "PostgreSQL user [bot]: " database_user
database_user="${database_user:-bot}"
read -r -s -p "PostgreSQL password [bot]: " database_password
echo
database_password="${database_password:-bot}"
read -r -p "PostgreSQL host [127.0.0.1]: " database_host
database_host="${database_host:-127.0.0.1}"
read -r -p "PostgreSQL port [5432]: " database_port
database_port="${database_port:-5432}"
[[ "$database_port" =~ ^[1-9][0-9]*$ ]] || fail "PostgreSQL port must be a positive integer"

database_url="postgresql+asyncpg://$database_user:$database_password@$database_host:$database_port/$database_name"
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
