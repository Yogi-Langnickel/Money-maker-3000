from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from money_maker_3000.contracts import ValidationResult

BLOCKED_STATUS_VALUES = ("blocked", "absent", "not-loaded", "metadata-only")

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
        "capabilities": {
            "portfolioRead": "absent",
            "marketDataRead": "absent",
            "newsRead": "absent",
            "orderPreview": "absent",
            "demoOrders": "blocked",
            "liveOrders": "blocked",
        },
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

    capabilities = provider.get("capabilities")
    if not isinstance(capabilities, dict):
        errors.append("provider capabilities are required")
    else:
        for capability, value in capabilities.items():
            if value not in BLOCKED_STATUS_VALUES:
                errors.append(f"provider capability must be unavailable: {capability}")
    return ValidationResult(ok=not errors, errors=tuple(errors))


def build_provider_metadata_snapshot(providers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source_providers = providers if providers is not None else PROVIDER_REGISTRY
    validations = [
        {
            "providerId": provider.get("providerId", "unknown") if isinstance(provider, dict) else "unknown",
            **validate_provider_metadata(provider).to_dict(),
        }
        for provider in source_providers
    ]
    safe_providers = []
    for provider in source_providers:
        source = provider if isinstance(provider, dict) else {}
        safe_providers.append(
            {
                "providerId": source.get("providerId", "unknown"),
                "displayName": source.get("displayName", "Unknown provider"),
                "status": source.get("status", "invalid"),
                "providerCalls": source.get("providerCalls", "invalid"),
                "credentials": source.get("credentials", "invalid"),
                "accountData": source.get("accountData", "invalid"),
                "marketData": source.get("marketData", "invalid"),
                "orderPreview": source.get("orderPreview", "invalid"),
                "demoExecution": source.get("demoExecution", "invalid"),
                "liveExecution": source.get("liveExecution", "invalid"),
                "supportedModes": list(source.get("supportedModes", [])),
                "capabilities": deepcopy(source.get("capabilities", {})),
            }
        )
    return {
        "mode": "metadata-only",
        "providerCalls": "blocked",
        "credentials": "not-loaded",
        "accountData": "absent",
        "executionRoutes": "absent",
        "providers": safe_providers,
        "validation": {
            "ok": all(validation["ok"] for validation in validations),
            "providers": validations,
        },
    }
