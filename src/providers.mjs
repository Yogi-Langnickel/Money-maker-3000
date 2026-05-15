const BLOCKED_STATUS_VALUES = Object.freeze(["blocked", "absent", "not-loaded", "metadata-only"]);

export const PROVIDER_REGISTRY = Object.freeze([
  Object.freeze({
    providerId: "etoro",
    displayName: "eToro",
    status: "metadata-only",
    providerCalls: "blocked",
    credentials: "not-loaded",
    accountData: "absent",
    marketData: "absent",
    orderPreview: "absent",
    demoExecution: "blocked",
    liveExecution: "blocked",
    supportedModes: Object.freeze(["simulation"]),
    capabilities: Object.freeze({
      portfolioRead: "absent",
      marketDataRead: "absent",
      newsRead: "absent",
      orderPreview: "absent",
      demoOrders: "blocked",
      liveOrders: "blocked",
    }),
  }),
]);

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function includesBlockedValue(value) {
  return BLOCKED_STATUS_VALUES.includes(value);
}

export function validateProviderMetadata(provider) {
  const errors = [];

  if (!isPlainObject(provider)) {
    return {
      ok: false,
      errors: ["provider metadata must be an object"],
    };
  }

  if (!provider.providerId || typeof provider.providerId !== "string") {
    errors.push("provider id is required");
  }

  if (!provider.displayName || typeof provider.displayName !== "string") {
    errors.push("provider display name is required");
  }

  if (provider.status !== "metadata-only") {
    errors.push("provider status must remain metadata-only");
  }

  if (provider.providerCalls !== "blocked") {
    errors.push("provider calls must be blocked");
  }

  if (provider.credentials !== "not-loaded") {
    errors.push("provider credentials must not be loaded");
  }

  if (provider.accountData !== "absent") {
    errors.push("provider account data must be absent");
  }

  if (provider.marketData !== "absent") {
    errors.push("provider market data must be absent");
  }

  if (provider.orderPreview !== "absent") {
    errors.push("provider order preview must be absent");
  }

  if (provider.demoExecution !== "blocked") {
    errors.push("provider demo execution must be blocked");
  }

  if (provider.liveExecution !== "blocked") {
    errors.push("provider live execution must be blocked");
  }

  if (!Array.isArray(provider.supportedModes) || provider.supportedModes.length !== 1) {
    errors.push("provider supported modes must contain simulation only");
  } else if (provider.supportedModes[0] !== "simulation") {
    errors.push("provider supported modes must contain simulation only");
  }

  if (!isPlainObject(provider.capabilities)) {
    errors.push("provider capabilities are required");
  } else {
    for (const [capability, value] of Object.entries(provider.capabilities)) {
      if (!includesBlockedValue(value)) {
        errors.push(`provider capability must be unavailable: ${capability}`);
      }
    }
  }

  return {
    ok: errors.length === 0,
    errors,
  };
}

export function buildProviderMetadataSnapshot({ providers = PROVIDER_REGISTRY } = {}) {
  const validations = providers.map((provider) => ({
    providerId: provider?.providerId ?? "unknown",
    ...validateProviderMetadata(provider),
  }));

  return Object.freeze({
    mode: "metadata-only",
    providerCalls: "blocked",
    credentials: "not-loaded",
    accountData: "absent",
    executionRoutes: "absent",
    providers: Object.freeze(
      providers.map((provider) => {
        const source = isPlainObject(provider) ? provider : {};
        const supportedModes = Array.isArray(source.supportedModes) ? source.supportedModes : [];
        const capabilities = isPlainObject(source.capabilities) ? source.capabilities : {};

        return Object.freeze({
          providerId: source.providerId ?? "unknown",
          displayName: source.displayName ?? "Unknown provider",
          status: source.status ?? "invalid",
          providerCalls: source.providerCalls ?? "invalid",
          credentials: source.credentials ?? "invalid",
          accountData: source.accountData ?? "invalid",
          marketData: source.marketData ?? "invalid",
          orderPreview: source.orderPreview ?? "invalid",
          demoExecution: source.demoExecution ?? "invalid",
          liveExecution: source.liveExecution ?? "invalid",
          supportedModes: Object.freeze([...supportedModes]),
          capabilities: Object.freeze({ ...capabilities }),
        });
      }),
    ),
    validation: Object.freeze({
      ok: validations.every((validation) => validation.ok),
      providers: Object.freeze(validations),
    }),
  });
}
