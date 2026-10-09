"""提示词与任务分配路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.presenters import (
    compile_response,
    container,
    prompt_response,
    prompt_test_response,
    task_setting_response,
)
from rss_v2.api.schemas import (
    PromptCompileRequest,
    PromptCompileResponse,
    PromptCreateRequest,
    PromptResponse,
    PromptTestRequest,
    PromptTestResponse,
    TaskSettingResponse,
    TaskSettingsUpdateRequest,
)
from rss_v2.domain import DomainError, PromptSample, TaskSetting
from rss_v2.domain.prompts import TASK_SPECS, compile_prompt, prompt_values

prompts_router = APIRouter(prefix="/api/llm/prompts", tags=["prompts"])
task_settings_router = APIRouter(prefix="/api/task-settings", tags=["task-settings"])


@prompts_router.get("", response_model=list[PromptResponse])
def list_prompts(
    request: Request, task_kind: str | None = Query(default=None, max_length=60)
) -> list[PromptResponse]:
    """列出提示词版本；可按任务类型过滤。"""
    return [prompt_response(item) for item in container(request).prompt_service.list(task_kind)]


@prompts_router.get("/specs", response_model=list[dict[str, object]])
def list_specs() -> list[dict[str, object]]:
    """任务规格：界面用它渲染占位符提示与输出字段要求。"""

    return [
        {
            "task_kind": spec.kind.value,
            "label": spec.label,
            "variables": list(spec.variables),
            "required_variables": list(spec.required_variables),
            "output_fields": list(spec.output_fields),
        }
        for spec in TASK_SPECS.values()
    ]


@prompts_router.post("/compile", response_model=PromptCompileResponse)
def compile_prompt_template(payload: PromptCompileRequest) -> PromptCompileResponse:
    """编译校验与样例预览；不写库。"""

    sample = payload.sample
    values = prompt_values(sample.title, sample.summary, sample.content) if sample else None
    compiled = compile_prompt(
        payload.task_kind, payload.system_template, payload.user_template, values
    )
    return compile_response(compiled, payload.task_kind)


@prompts_router.post("", response_model=PromptResponse, status_code=status.HTTP_201_CREATED)
def create_prompt(request: Request, payload: PromptCreateRequest) -> PromptResponse:
    """新建提示词版本；同 prompt_key 的版本号自动递增。"""
    prompt = container(request).prompt_service.create_version(
        payload.task_kind,
        payload.prompt_key,
        payload.name,
        payload.system_template,
        payload.user_template,
        payload.note,
    )
    return prompt_response(prompt)


@prompts_router.get("/{prompt_id}", response_model=PromptResponse)
def get_prompt(prompt_id: str, request: Request) -> PromptResponse:
    return prompt_response(container(request).prompt_service.get(prompt_id))


@prompts_router.post("/{prompt_id}/activate", response_model=PromptResponse)
def activate_prompt(prompt_id: str, request: Request) -> PromptResponse:
    """启用版本；同任务类型的其它启用版本自动归档。"""
    return prompt_response(container(request).prompt_service.activate(prompt_id))


@prompts_router.post("/{prompt_id}/archive", response_model=PromptResponse)
def archive_prompt(prompt_id: str, request: Request) -> PromptResponse:
    return prompt_response(container(request).prompt_service.archive(prompt_id))


@prompts_router.post("/{prompt_id}/test", response_model=PromptTestResponse)
def test_prompt(prompt_id: str, request: Request, payload: PromptTestRequest) -> PromptTestResponse:
    """用样例输入试跑；会消耗一次真实模型调用，只写调用审计。"""
    services = container(request)
    if payload.message_version_id:
        sample = services.message_service.version_sample(payload.message_version_id)
    elif payload.sample:
        sample = PromptSample(payload.sample.title, payload.sample.summary, payload.sample.content)
    else:
        raise DomainError("sample_required", "试跑需要选择一条消息或填写样例文本")
    return prompt_test_response(services.prompt_service.test(prompt_id, sample, payload.model))


@task_settings_router.get("/{scope}/{scope_id}", response_model=list[TaskSettingResponse])
def get_task_settings(scope: str, scope_id: str, request: Request) -> list[TaskSettingResponse]:
    """某来源或分类的任务分配；带生效值与来源层级。"""
    return [
        task_setting_response(item)
        for item in container(request).task_setting_service.describe(scope, scope_id)
    ]


@task_settings_router.put("/{scope}/{scope_id}", response_model=list[TaskSettingResponse])
def replace_task_settings(
    scope: str, scope_id: str, request: Request, payload: TaskSettingsUpdateRequest
) -> list[TaskSettingResponse]:
    """整组覆盖任务分配；空值表示继承上一层。"""
    items = [
        TaskSetting(scope, scope_id, item.task_kind, item.enabled, item.prompt_id, 0, 0)
        for item in payload.settings
    ]
    return [
        task_setting_response(item)
        for item in container(request).task_setting_service.replace(scope, scope_id, items)
    ]
