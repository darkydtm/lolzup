import enum
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
