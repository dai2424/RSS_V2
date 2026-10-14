"""路由集合的公开导出。"""

from .routers.categories import categories_router
from .routers.collection import collection_router
from .routers.keyword_pin_suggestions import pin_suggestions_router
from .routers.keyword_suggestions import suggestions_router
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
    "suggestions_router",
    "pin_suggestions_router",
    "messages_router",
    "tasks_router",
    "prompts_router",
    "providers_router",
    "task_settings_router",
]
