import { buildSimulationRun } from "./contracts.mjs";

export function runOnce(options = {}) {
  return buildSimulationRun(options);
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  const result = runOnce();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}
