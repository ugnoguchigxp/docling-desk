import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { openapi, ROUTES } from "../src/app";
import { responseSchemas } from "../src/responses";

const path = new URL("../../contracts/knowledge-api.openapi.json", import.meta.url);
const expected = `${JSON.stringify(openapi(), null, 2)}\n`;
for (const [method, route] of ROUTES) {
  if (!route.endsWith("/content") && !responseSchemas[`${method} ${route}`])
    throw new Error(`Missing response schema: ${method} ${route}`);
}
if (process.argv.includes("--write")) {
  mkdirSync(dirname(path.pathname), { recursive: true });
  writeFileSync(path, expected);
}
if (readFileSync(path, "utf8") !== expected)
  throw new Error(
    "OpenAPI differs from runtime schemas. Review the contract, then run contract:check --write.",
  );
console.log("OpenAPI input/output schemas and saved contract match.");
