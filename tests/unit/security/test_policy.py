import pytest

from lolzup.db.models import EncryptionMode
from lolzup.security.policy import DataCategory, EncryptionPolicy


@pytest.mark.unit
@pytest.mark.parametrize("category", list(DataCategory))
def test_full_policy_encrypts_every_category(category: DataCategory) -> None:
	policy = EncryptionPolicy(EncryptionMode.FULL)

	assert policy.encrypts(category)


@pytest.mark.unit
@pytest.mark.parametrize("category", list(DataCategory))
def test_disabled_policy_encrypts_no_categories(category: DataCategory) -> None:
	policy = EncryptionPolicy(EncryptionMode.DISABLED)

	assert not policy.encrypts(category)


@pytest.mark.unit
def test_custom_policy_encrypts_selected_categories_and_secrets() -> None:
	policy = EncryptionPolicy(
		EncryptionMode.CUSTOM,
		frozenset({DataCategory.TOPICS, DataCategory.HISTORY}),
	)

	assert policy.encrypts(DataCategory.SECRETS)
	assert policy.encrypts(DataCategory.TOPICS)
	assert policy.encrypts(DataCategory.HISTORY)
	assert not policy.encrypts(DataCategory.SCHEDULING)
	assert not policy.encrypts(DataCategory.TELEGRAM_IDENTITIES)
