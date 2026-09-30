from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from naos_api.errors import PolicyError
from naos_api.model import ModelPolicyIn, resolve_model_policy
from naos_api.policies import create_model_policy
from naos_api.spec import PolicyKind


def _provider(**values: Any) -> dict[str, Any]:
    return {
        "name": "alpha",
        "api": "openai",
        "url": "https://models.example.com",
        "credential": "alpha-key",
        "models": ["alpha-mini"],
    } | values


def _policy(*providers: dict[str, Any], **values: Any) -> ModelPolicyIn:
    document = {"providers": list(providers), "max_input_tokens": 1000, "max_output_tokens": 100}
    return ModelPolicyIn.model_validate(document | values)


def test_providers_are_canonical() -> None:
    resolved = resolve_model_policy(
        _policy(
            _provider(name="beta", api="anthropic", url="https://BETA.example.com:443/"),
            _provider(url="https://models.example.com:8443/api/", models=["b", "a"]),
        )
    )

    assert [provider.name for provider in resolved.providers] == ["alpha", "beta"]
    assert resolved.providers[0].url == "https://models.example.com:8443/api"
    assert resolved.providers[0].models == ["a", "b"]
    assert resolved.providers[1].url == "https://beta.example.com"
    assert resolved.providers[1].timeout_seconds == 600
    assert resolved.providers[1].max_requests_per_minute == 60
    assert (resolved.max_input_tokens, resolved.max_output_tokens) == (1000, 100)


def test_equivalent_documents_share_one_digest(session: Session) -> None:
    first, created = create_model_policy(session, _policy(_provider(models=["a", "b"])))
    again, created_again = create_model_policy(session, _policy(_provider(models=["b", "a"])))

    assert created and not created_again
    assert first.id == again.id
    assert first.id.startswith("modelpol_")
    assert first.kind is PolicyKind.MODEL


@pytest.mark.parametrize(
    "providers",
    [
        [],
        [_provider(), _provider()],
        [_provider(models=["a", "a"])],
        [_provider(models=["a"]), _provider(name="beta", models=["a"])],
        [_provider(url="http://models.example.com")],
        [_provider(url="https://user:pass@models.example.com")],
        [_provider(url="https://models.example.com/?key=alpha")],
        [_provider(url="https://models.example.com/#alpha")],
        [_provider(url="https://192.0.2.10")],
        [_provider(url="https://localhost")],
    ],
)
def test_unusable_policies_are_refused(providers: list[dict[str, Any]]) -> None:
    with pytest.raises(PolicyError):
        resolve_model_policy(_policy(*providers))


@pytest.mark.parametrize(
    "provider",
    [
        _provider(name="Alpha"),
        _provider(api="gemini"),
        _provider(credential=None),
        _provider(credential="Bad Name"),
        _provider(models=[]),
        _provider(models=["bad model"]),
        _provider(timeout_seconds=0),
        _provider(timeout_seconds=901),
        _provider(max_requests_per_minute=601),
        _provider(extra=1),
    ],
)
def test_malformed_providers_are_refused_by_the_model(provider: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _policy(provider)


@pytest.mark.parametrize(
    "values",
    [
        {"max_input_tokens": 0},
        {"max_output_tokens": None},
        {"max_output_tokens": "10"},
    ],
)
def test_the_run_budget_is_required(values: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _policy(_provider(), **values)


def test_the_api_creates_and_names_a_model_policy(
    client: TestClient, model_body: dict[str, Any]
) -> None:
    created = client.post("/api/v1/policies", json={"kind": "model", "document": model_body})
    listed = client.get("/api/v1/policies", params={"kind": "model"})

    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "model"
    assert [row["id"] for row in listed.json()] == [created.json()["id"]]


def test_the_api_refuses_a_model_on_two_providers(
    client: TestClient, model_body: dict[str, Any]
) -> None:
    model_body["providers"][1]["models"] = ["alpha-mini"]

    response = client.post("/api/v1/policies", json={"kind": "model", "document": model_body})

    assert response.status_code == 422, response.text
