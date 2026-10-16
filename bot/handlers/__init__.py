# Роутеры: о боте, настройки

from ..tg_shim import Router

from .about import router as about_router
from .settings import router as settings_router

router = Router(name="bot_handlers")
router.include_router(about_router)
router.include_router(settings_router)
