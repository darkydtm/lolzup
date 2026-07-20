#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env"
venv_dir="$project_dir/.venv"
container_name="${LOLZUP_POSTGRES_CONTAINER:-lolzup-postgres}"

fail() {
	echo "Error: $*" >&2
	exit 1
}

command -v python3.12 >/dev/null 2>&1 || fail "Python 3.12 is required"
command -v docker >/dev/null 2>&1 || fail "Docker is required"

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
read -r -p "PostgreSQL port [5432]: " database_port
database_port="${database_port:-5432}"
[[ "$database_port" =~ ^[1-9][0-9]*$ ]] || fail "PostgreSQL port must be a positive integer"

if docker container inspect "$container_name" >/dev/null 2>&1; then
	docker start "$container_name" >/dev/null || true
else
	docker run -d --name "$container_name" \
		-e "POSTGRES_DB=$database_name" \
		-e "POSTGRES_USER=$database_user" \
		-e "POSTGRES_PASSWORD=$database_password" \
		-p "$database_port:5432" \
		postgres:17 >/dev/null
fi

echo "Waiting for PostgreSQL..."
for attempt in {1..30}; do
	if docker exec "$container_name" pg_isready -U "$database_user" -d "$database_name" >/dev/null 2>&1; then
		break
	fi
	sleep 1
done
docker exec "$container_name" pg_isready -U "$database_user" -d "$database_name" >/dev/null 2>&1 \
	|| fail "PostgreSQL did not become ready"

database_url="postgresql+asyncpg://$database_user:$database_password@127.0.0.1:$database_port/$database_name"
umask 077
printf 'BOT_TOKEN=%s\nOWNER_ID=%s\nDATABASE_URL=%s\nLOG_LEVEL=INFO\nSCHEDULER_POLL_SECONDS=60\n' \
	"$bot_token" "$owner_id" "$database_url" > "$env_file"

python3.12 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install -e "$project_dir"

cd "$project_dir"
"$venv_dir/bin/alembic" upgrade head

echo
echo "Installation completed. Start the bot with:"
echo "  ./scripts/run.sh"
