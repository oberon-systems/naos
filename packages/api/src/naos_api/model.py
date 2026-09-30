from typing import Annotated, Literal

from pydantic import Field, StrictInt

from naos_api.errors import PolicyError
from naos_api.mcp import RawUrl, https_url
from naos_api.secrets import SecretName
from naos_api.spec import StrictModel

MAX_PROVIDERS = 16
MAX_MODELS = 64
MAX_TOKENS = 10_000_000_000

ProviderName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]
ModelName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")]
Dialect = Literal["openai", "anthropic"]
Tokens = Annotated[StrictInt, Field(ge=1, le=MAX_TOKENS)]


class ProviderIn(StrictModel):
    name: ProviderName
    api: Dialect
    url: RawUrl
    credential: SecretName
    models: Annotated[list[ModelName], Field(min_length=1, max_length=MAX_MODELS)]
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=900)] = 600
    max_requests_per_minute: Annotated[StrictInt, Field(ge=1, le=600)] = 60


class ModelPolicyIn(StrictModel):
    providers: Annotated[list[ProviderIn], Field(max_length=MAX_PROVIDERS)] = []
    max_input_tokens: Tokens
    max_output_tokens: Tokens


class Provider(StrictModel):
    name: str
    api: Dialect
    url: str
    credential: str
    models: list[str]
    timeout_seconds: int
    max_requests_per_minute: int


class ModelPolicy(StrictModel):
    providers: list[Provider]
    max_input_tokens: int
    max_output_tokens: int


def _provider(provider: ProviderIn) -> Provider:
    if len(set(provider.models)) != len(provider.models):
        raise PolicyError(f"provider {provider.name} repeats a model")
    return Provider(
        name=provider.name,
        api=provider.api,
        # The gateway appends the request path, so the base keeps no trailing slash.
        url=https_url(provider.url, "provider").rstrip("/"),
        credential=provider.credential,
        models=sorted(provider.models),
        timeout_seconds=provider.timeout_seconds,
        max_requests_per_minute=provider.max_requests_per_minute,
    )


def resolve_model_policy(policy: ModelPolicyIn) -> ModelPolicy:
    if not policy.providers:
        raise PolicyError("a model policy must name at least one provider")
    names = [provider.name for provider in policy.providers]
    if len(set(names)) != len(names):
        raise PolicyError("provider names must be unique")
    providers = sorted((_provider(p) for p in policy.providers), key=lambda p: p.name)
    served: dict[str, str] = {}
    for provider in providers:
        for model in provider.models:
            if model in served:
                raise PolicyError(f"model {model} is served by {served[model]} and {provider.name}")
            served[model] = provider.name
    return ModelPolicy(
        providers=providers,
        max_input_tokens=policy.max_input_tokens,
        max_output_tokens=policy.max_output_tokens,
    )
