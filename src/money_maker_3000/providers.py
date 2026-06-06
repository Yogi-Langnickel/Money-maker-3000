from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from money_maker_3000.contracts import ValidationResult

BLOCKED_STATUS_VALUES = ("blocked", "absent", "not-loaded", "metadata-only")
EXPECTED_PROVIDER_SAFETY_POSTURE = {
    "credentialLoading": "blocked",
    "privateAccountData": "absent",
    "rawPayloadPersistence": "blocked",
    "portfolioBalanceUse": "blocked-for-sizing",
    "orderMutation": "blocked",
    "networkAccess": "absent",
}
EXPECTED_PROVIDER_CAPABILITIES = {
    "portfolioRead": "absent",
    "marketDataRead": "absent",
    "newsRead": "absent",
    "orderPreview": "absent",
    "demoOrders": "blocked",
    "liveOrders": "blocked",
}

PROVIDER_REGISTRY = [
    {
        "providerId": "etoro",
        "displayName": "eToro",
        "status": "metadata-only",
        "providerCalls": "blocked",
        "credentials": "not-loaded",
        "accountData": "absent",
        "marketData": "absent",
        "orderPreview": "absent",
        "demoExecution": "blocked",
        "liveExecution": "blocked",
        "supportedModes": ["simulation"],
        "safetyPosture": deepcopy(EXPECTED_PROVIDER_SAFETY_POSTURE),
        "capabilities": deepcopy(EXPECTED_PROVIDER_CAPABILITIES),
    }
]


class ExecutionGateway(Protocol):
    def preview_order(self, intent: dict[str, Any]) -> dict[str, Any]:
        ...

    def submit_order(self, intent: dict[str, Any]) -> dict[str, Any]:
        ...


class DisabledExecutionGateway:
    provider_calls = "blocked"
    execution_routes = "absent"

    def preview_order(self, intent: dict[str, Any]) -> dict[str, Any]:
        raise PermissionError("order preview is disabled; provider mutation endpoints are absent")

    def submit_order(self, intent: dict[str, Any]) -> dict[str, Any]:
        raise PermissionError("demo/live execution is disabled; provider mutation endpoints are absent")


def validate_provider_metadata(provider: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    if not isinstance(provider, dict):
        return ValidationResult(ok=False, errors=("provider metadata must be an object",))
    if not isinstance(provider.get("providerId"), str) or not provider["providerId"]:
        errors.append("provider id is required")
    if not isinstance(provider.get("displayName"), str) or not provider["displayName"]:
        errors.append("provider display name is required")
    if provider.get("status") != "metadata-only":
        errors.append("provider status must remain metadata-only")
    if provider.get("providerCalls") != "blocked":
        errors.append("provider calls must be blocked")
    if provider.get("credentials") != "not-loaded":
        errors.append("provider credentials must not be loaded")
    if provider.get("accountData") != "absent":
        errors.append("provider account data must be absent")
    if provider.get("marketData") != "absent":
        errors.append("provider market data must be absent")
    if provider.get("orderPreview") != "absent":
        errors.append("provider order preview must be absent")
    if provider.get("demoExecution") != "blocked":
        errors.append("provider demo execution must be blocked")
    if provider.get("liveExecution") != "blocked":
        errors.append("provider live execution must be blocked")
    if provider.get("supportedModes") != ["simulation"]:
        errors.append("provider supported modes must contain simulation only")

    safety_posture = provider.get("safetyPosture")
    if not isinstance(safety_posture, dict):
        errors.append("provider safety posture is required")
    else:
        for key, expected in EXPECTED_PROVIDER_SAFETY_POSTURE.items():
            if safety_posture.get(key) != expected:
                errors.append(f"provider safety posture must keep {key}={expected}")

    capabilities = provider.get("capabilities")
    if not isinstance(capabilities, dict):
        errors.append("provider capabilities are required")
    else:
        for capability, value in capabilities.items():
            if value not in BLOCKED_STATUS_VALUES:
                errors.append("provider capability must be unavailable")
    return ValidationResult(ok=not errors, errors=tuple(errors))


def _known_provider_display_name(provider_id: str) -> str | None:
    for provider in PROVIDER_REGISTRY:
        if provider["providerId"] == provider_id:
            return provider["displayName"]
    return None


def _safe_provider_id(provider: dict[str, Any]) -> str:
    provider_id = provider.get("providerId")
    if isinstance(provider_id, str) and _known_provider_display_name(provider_id):
        return provider_id
    return "unknown"


def build_provider_metadata_snapshot(providers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source_providers = providers if providers is not None else PROVIDER_REGISTRY
    validations = [
        {
            "providerId": _safe_provider_id(provider) if isinstance(provider, dict) else "unknown",
            **validate_provider_metadata(provider).to_dict(),
        }
        for provider in source_providers
    ]
    safe_providers = []
    for provider in source_providers:
        source = provider if isinstance(provider, dict) else {}
        provider_id = _safe_provider_id(source)
        safe_providers.append(
            {
                "providerId": provider_id,
                "displayName": _known_provider_display_name(provider_id) or "Unknown provider",
                "status": "metadata-only",
                "providerCalls": "blocked",
                "credentials": "not-loaded",
                "accountData": "absent",
                "marketData": "absent",
                "orderPreview": "absent",
                "demoExecution": "blocked",
                "liveExecution": "blocked",
                "supportedModes": ["simulation"],
                "safetyPosture": deepcopy(EXPECTED_PROVIDER_SAFETY_POSTURE),
                "capabilities": deepcopy(EXPECTED_PROVIDER_CAPABILITIES),
            }
        )
    return {
        "mode": "metadata-only",
        "providerCalls": "blocked",
        "credentials": "not-loaded",
        "accountData": "absent",
        "executionRoutes": "absent",
        "safetyPosture": deepcopy(EXPECTED_PROVIDER_SAFETY_POSTURE),
        "providers": safe_providers,
        "validation": {
            "ok": all(validation["ok"] for validation in validations),
            "providers": validations,
        },
    }
