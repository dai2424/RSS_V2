"""路由集合的公开导出。"""

from .routers.categories import categories_router
from .routers.collection import collection_router
from .routers.keywords import keywords_router
from .routers.messages import messages_router
from .routers.prompts import prompts_router, task_settings_router
from .routers.providers import providers_router
from .routers.sources import sources_router
from .routers.tasks import tasks_router

__all__ = [
    "categories_router",
    "sources_router",
    "collection_router",
    "keywords_router",
    "messages_router",
    "tasks_router",
    "prompts_router",
    "providers_router",
    "task_settings_router",
]
