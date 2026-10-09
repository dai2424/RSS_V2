"""模型服务、提示词与任务分配的请求和响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .catalog import PatchRequest


class ProviderCreateRequest(BaseModel):
    """创建 provider 请求；protocol 省略时用 OpenAI 兼容协议。"""

    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=1000)
    protocol: str = Field(default="chat_completions", min_length=1, max_length=40)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    session_header_name: str | None = Field(default=None, max_length=100)
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ProviderPatchRequest(PatchRequest):
    """更新 provider 请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=8, max_length=1000)
    protocol: str | None = Field(default=None, min_length=1, max_length=40)
    enabled: bool | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    session_header_name: str | None = Field(default=None, max_length=100)
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ConnectionTestRequest(BaseModel):
    """连接测试请求；不填模型时使用第一个启用模型。"""

    model: str | None = Field(default=None, max_length=200)


class ConnectionTestResponse(BaseModel):
    """连接测试响应；只返回密钥掩码，不返回密钥值。"""

    ok: bool
    model: str
    key_masked: str
    latency_ms: int
    total_tokens: int
    error_code: str | None
    error_message: str | None


class ProviderModelCreateRequest(BaseModel):
    """在 provider 下新增模型候选请求。"""

    model: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=100, ge=0, le=10000)


class ProviderModelPatchRequest(PatchRequest):
    """更新模型候选请求。"""

    model: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)


class ProviderModelResponse(BaseModel):
    """模型候选响应。"""

    id: str
    provider_id: str
    model: str
    enabled: bool
    priority: int
    created_at: int
    updated_at: int


class ProviderKeyCreateRequest(BaseModel):
    """录入 API Key 请求；密钥值只写入本机数据库。"""

    secret: str = Field(min_length=1, max_length=500)
    priority: int = Field(default=100, ge=0, le=10000)


class ProviderKeyPatchRequest(PatchRequest):
    """更新 API Key 请求。"""

    secret: str | None = Field(default=None, min_length=1, max_length=500)
    priority: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class ProviderKeyResponse(BaseModel):
    """API Key 响应；只返回掩码，禁止返回密钥值。"""

    id: str
    provider_id: str
    masked: str
    priority: int
    enabled: bool
    cooldown_until: int | None
    last_status: str | None


class ProviderResponse(BaseModel):
    """provider 响应。"""

    id: str
    name: str
    base_url: str
    protocol: str
    enabled: bool
    timeout_seconds: float
    session_header_name: str | None
    keys: list[ProviderKeyResponse]
    models: list[ProviderModelResponse]
    extra_headers: dict[str, str]


class PromptCreateRequest(BaseModel):
    """新建提示词版本请求；编译不通过会被拒绝。"""

    task_kind: str = Field(min_length=1, max_length=60)
    prompt_key: str = Field(min_length=1, max_length=41)
    name: str = Field(default="", max_length=100)
    system_template: str = Field(default="", max_length=20000)
    user_template: str = Field(min_length=1, max_length=20000)
    note: str = Field(default="", max_length=500)


class PromptUpdateRequest(BaseModel):
    """就地编辑请求；任务类型、业务键与版本号不变，名称留空时回落到业务键。"""

    name: str = Field(default="", max_length=100)
    system_template: str = Field(default="", max_length=20000)
    user_template: str = Field(min_length=1, max_length=20000)
    note: str = Field(default="", max_length=500)


class PromptSampleRequest(BaseModel):
    """编译预览与试跑的样例输入。"""

    title: str = Field(default="", max_length=2000)
    summary: str = Field(default="", max_length=20000)
    content: str = Field(default="", max_length=50000)


class PromptCompileRequest(BaseModel):
    """编译校验请求；只校验和预览，不写库。"""

    task_kind: str = Field(min_length=1, max_length=60)
    system_template: str = Field(default="", max_length=20000)
    user_template: str = Field(default="", max_length=20000)
    sample: PromptSampleRequest | None = None


class PromptCompileIssue(BaseModel):
    """一条编译问题。"""

    field: str
    message: str


class PromptCompileResponse(BaseModel):
    """编译结果：问题清单与样例渲染结果。"""

    ok: bool
    errors: list[PromptCompileIssue]
    warnings: list[PromptCompileIssue]
    system: str
    user: str
    variables: list[str]


class PromptTestRequest(BaseModel):
    """试跑请求；用真实消息或手填样例，会消耗一次模型调用。"""

    message_version_id: str | None = None
    sample: PromptSampleRequest | None = None
    model: str | None = Field(default=None, max_length=200)


class PromptTestResponse(BaseModel):
    """试跑结果；只返回密钥掩码与结构化输出。"""

    ok: bool
    model: str
    key_masked: str
    latency_ms: int
    total_tokens: int
    system: str
    user: str
    output: dict[str, Any]
    error_code: str | None
    error_message: str | None


class PromptResponse(BaseModel):
    """提示词版本响应。"""

    id: str
    task_kind: str
    prompt_key: str
    version: int
    version_string: str
    name: str
    status: str
    system_template: str
    user_template: str
    note: str
    created_at: int
    updated_at: int


class PromptUsageResponse(BaseModel):
    """提示词版本的使用情况；界面据此决定能否就地编辑或删除。

    `used` 为真时只能另存新版本或归档；`bindings` 是来源与分类对该版本的绑定条数。
    """

    used: bool
    calls: int
    results: int
    tasks: int
    bindings: int


class TaskSettingItem(BaseModel):
    """单个任务类型的分配；enabled 与 prompt_id 为空表示继承上一层。"""

    task_kind: str = Field(min_length=1, max_length=60)
    enabled: bool | None = None
    prompt_id: str | None = None


class TaskSettingsUpdateRequest(BaseModel):
    """整组覆盖某作用域的任务分配；列表为空表示全部回到继承。"""

    settings: list[TaskSettingItem] = Field(max_length=50)


class TaskSettingResponse(BaseModel):
    """任务分配响应；带生效值与来源层级，便于界面显示"继承"还是"已覆盖"。"""

    task_kind: str
    label: str
    enabled: bool | None
    prompt_id: str | None
    effective_enabled: bool
    effective_prompt_id: str | None
    effective_prompt_version: str | None
    scope: str
