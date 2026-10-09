"""providers 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from rss_v2.api.presenters import (
    container,
    provider_key_response,
    provider_model_response,
    provider_response,
)
from rss_v2.api.schemas import (
    ConnectionTestRequest,
    ConnectionTestResponse,
    ProviderCreateRequest,
    ProviderKeyCreateRequest,
    ProviderKeyPatchRequest,
    ProviderKeyResponse,
    ProviderModelCreateRequest,
    ProviderModelPatchRequest,
    ProviderModelResponse,
    ProviderPatchRequest,
    ProviderResponse,
)

providers_router = APIRouter(prefix="/api/llm/providers", tags=["llm"])


@providers_router.get("", response_model=list[ProviderResponse])
def list_providers(request: Request) -> list[ProviderResponse]:
    return [
        provider_response(provider, models, keys)
        for provider, models, keys in container(request).provider_service.list()
    ]


@providers_router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(payload: ProviderCreateRequest, request: Request) -> ProviderResponse:
    current = container(request)
    provider = current.provider_service.create(
        payload.name,
        payload.base_url,
        payload.timeout_seconds,
        payload.session_header_name,
        payload.extra_headers,
        payload.protocol,
    )
    return provider_response(provider, [], [])


@providers_router.patch("/{provider_id}", response_model=ProviderResponse)
def update_provider(
    provider_id: str, payload: ProviderPatchRequest, request: Request
) -> ProviderResponse:
    current = container(request)
    provider = current.provider_service.update(provider_id, payload.model_dump(exclude_unset=True))
    return provider_response(
        provider,
        current.provider_service.models(provider.id),
        current.provider_service.keys(provider.id),
    )


@providers_router.post("/{provider_id}/test", response_model=ConnectionTestResponse)
def test_provider_connection(
    provider_id: str, payload: ConnectionTestRequest, request: Request
) -> ConnectionTestResponse:
    """试跑一次最小结构化调用，验证 Base URL、模型与 Key 是否可用。"""

    result = container(request).translation_service.test_connection(provider_id, payload.model)
    return ConnectionTestResponse.model_validate(result, from_attributes=True)


@providers_router.post(
    "/{provider_id}/models",
    response_model=ProviderModelResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_provider_model(
    provider_id: str, payload: ProviderModelCreateRequest, request: Request
) -> ProviderModelResponse:
    model = container(request).provider_service.create_model(
        provider_id, payload.model, payload.priority
    )
    return provider_model_response(model)


@providers_router.patch("/{provider_id}/models/{model_id}", response_model=ProviderModelResponse)
def update_provider_model(
    provider_id: str, model_id: str, payload: ProviderModelPatchRequest, request: Request
) -> ProviderModelResponse:
    model = container(request).provider_service.update_model(
        provider_id, model_id, payload.model_dump(exclude_unset=True)
    )
    return provider_model_response(model)


@providers_router.post(
    "/{provider_id}/keys", response_model=ProviderKeyResponse, status_code=status.HTTP_201_CREATED
)
def create_provider_key(
    provider_id: str, payload: ProviderKeyCreateRequest, request: Request
) -> ProviderKeyResponse:
    key = container(request).provider_service.add_key(provider_id, payload.secret, payload.priority)
    return provider_key_response(key)


@providers_router.patch("/{provider_id}/keys/{key_id}", response_model=ProviderKeyResponse)
def update_provider_key(
    provider_id: str, key_id: str, payload: ProviderKeyPatchRequest, request: Request
) -> ProviderKeyResponse:
    key = container(request).provider_service.update_key(
        provider_id, key_id, payload.model_dump(exclude_unset=True)
    )
    return provider_key_response(key)
