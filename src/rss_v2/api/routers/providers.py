"""providers 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from rss_v2.api.presenters import (
    container,
    provider_key_response,
    provider_response,
)
from rss_v2.api.schemas import (
    ProviderCreateRequest,
    ProviderKeyCreateRequest,
    ProviderKeyPatchRequest,
    ProviderKeyResponse,
    ProviderPatchRequest,
    ProviderResponse,
)

providers_router = APIRouter(prefix="/api/llm/providers", tags=["llm"])


@providers_router.get("", response_model=list[ProviderResponse])
def list_providers(request: Request) -> list[ProviderResponse]:
    return [
        provider_response(provider, keys)
        for provider, keys in container(request).provider_service.list()
    ]


@providers_router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(payload: ProviderCreateRequest, request: Request) -> ProviderResponse:
    current = container(request)
    provider = current.provider_service.create(
        payload.name,
        payload.base_url,
        payload.model,
        payload.priority,
        payload.timeout_seconds,
        payload.session_header_name,
        payload.extra_headers,
    )
    return provider_response(provider, [])


@providers_router.patch("/{provider_id}", response_model=ProviderResponse)
def update_provider(
    provider_id: str, payload: ProviderPatchRequest, request: Request
) -> ProviderResponse:
    current = container(request)
    provider = current.provider_service.update(provider_id, payload.model_dump(exclude_unset=True))
    return provider_response(provider, current.provider_service.keys(provider.id))


@providers_router.post(
    "/{provider_id}/keys", response_model=ProviderKeyResponse, status_code=status.HTTP_201_CREATED
)
def create_provider_key(
    provider_id: str, payload: ProviderKeyCreateRequest, request: Request
) -> ProviderKeyResponse:
    key = container(request).provider_service.add_key(
        provider_id, payload.key_ref, payload.priority
    )
    return provider_key_response(key)


@providers_router.patch("/{provider_id}/keys/{key_id}", response_model=ProviderKeyResponse)
def update_provider_key(
    provider_id: str, key_id: str, payload: ProviderKeyPatchRequest, request: Request
) -> ProviderKeyResponse:
    key = container(request).provider_service.update_key(
        provider_id, key_id, payload.model_dump(exclude_unset=True)
    )
    return provider_key_response(key)
