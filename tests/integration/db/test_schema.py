import asyncio
import os

import pytest
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from lolzup.db.models import Base

EXPECTED_TABLES = {
	"administrators",
	"app_settings",
	"bump_attempts",
	"encryption_migrations",
	"known_users",
	"menu_references",
	"secret_envelopes",
	"topics",
}


@pytest.mark.integration
def test_initial_schema_contains_required_tables_and_constraints() -> None:
	async def inspect_schema() -> None:
		engine = create_async_engine(os.environ["DATABASE_URL"])
		async with engine.begin() as connection:
			await connection.run_sync(Base.metadata.drop_all)
			await connection.run_sync(Base.metadata.create_all)

			def assert_schema(sync_connection: Connection) -> None:
				inspector = inspect(sync_connection)
				assert EXPECTED_TABLES <= set(inspector.get_table_names())
				settings_checks = inspector.get_check_constraints("app_settings")
				assert any(
					constraint["name"] == "ck_app_settings_singleton"
					for constraint in settings_checks
				)
				topic_unique = inspector.get_unique_constraints("topics")
				assert any(
					constraint["column_names"] == ["thread_id_index"]
					for constraint in topic_unique
				)
				attempt_foreign_keys = inspector.get_foreign_keys("bump_attempts")
				assert any(
					foreign_key["referred_table"] == "topics"
					for foreign_key in attempt_foreign_keys
				)
				next_bump_type = inspector.get_columns("topics")
				next_bump_column = next(
					column
					for column in next_bump_type
					if column["name"] == "next_bump_at_plain"
				)
				assert next_bump_column["type"].timezone is True

			await connection.run_sync(assert_schema)
		await engine.dispose()

	asyncio.run(inspect_schema())
