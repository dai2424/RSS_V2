"""领域和服务层错误。"""


class DomainError(Exception):
    """可向 API 暴露的业务错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(DomainError):
    """资源不存在。"""


class ConflictError(DomainError):
    """请求与现有状态冲突。"""


class ExternalServiceError(DomainError):
    """外部 RSS 或 LLM 服务失败。"""

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(code, message)
        self.status_code = status_code
