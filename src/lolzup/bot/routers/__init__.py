from aiogram import Router

from lolzup.bot.routers.admins import build_admins_router
from lolzup.bot.routers.cancel import build_cancel_router
from lolzup.bot.routers.menu import build_menu_router
from lolzup.bot.routers.settings import build_settings_router
from lolzup.bot.routers.setup import build_setup_router
from lolzup.bot.routers.topics import build_topics_router


def build_routers() -> tuple[Router, ...]:
	return (
		build_cancel_router(),
		build_setup_router(),
		build_topics_router(),
		build_settings_router(),
		build_admins_router(),
		build_menu_router(),
	)


__all__ = [
	"build_routers",
]
