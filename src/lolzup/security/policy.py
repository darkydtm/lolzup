import enum
import json
from dataclasses import dataclass, field

from lolzup.db.models import EncryptionMode


class DataCategory(enum.StrEnum):
	SECRETS = "secrets"
	TOPICS = "topics"
	SCHEDULING = "scheduling"
	HISTORY = "history"
	TELEGRAM_IDENTITIES = "telegram_identities"


@dataclass(frozen=True, slots=True)
class EncryptionPolicy:
	mode: EncryptionMode
	categories: frozenset[DataCategory] = field(default_factory=frozenset)

	def encrypts(self, category: DataCategory) -> bool:
		if self.mode is EncryptionMode.DISABLED:
			return False
		if category is DataCategory.SECRETS:
			return True
		if self.mode is EncryptionMode.FULL:
			return True
		return category in self.categories

	def serialize(self) -> str:
		return json.dumps(
			{
				"mode": self.mode.value,
				"categories": sorted(category.value for category in self.categories),
			},
			separators=(",", ":"),
		)

	@classmethod
	def deserialize(cls, value: str) -> "EncryptionPolicy":
		payload = json.loads(value)
		if not isinstance(payload, dict):
			raise ValueError("Encryption policy payload must be an object")
		mode = EncryptionMode(payload["mode"])
		raw_categories = payload.get("categories", [])
		if not isinstance(raw_categories, list):
			raise ValueError("Encryption policy categories must be a list")
		categories = frozenset(DataCategory(category) for category in raw_categories)
		return cls(mode, categories)
