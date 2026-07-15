from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import PostgresDsn, PositiveInt, SecretStr


class Settings(BaseSettings):
	model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

	bot_token: SecretStr
	owner_id: PositiveInt
	database_url: PostgresDsn
	log_level: str = "INFO"
	scheduler_poll_seconds: PositiveInt = 60
