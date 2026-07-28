// Verify web/lib/types.ts still matches the server's OpenAPI schema.
//
// Usage:  node scripts/check-schema-sync.mjs <openapi.json>
//
// WHAT IS COMPARED: property names, and whether each is required or optional.
// WHAT IS NOT: types.
//
// Type comparison is a dead end here and would be permanently red. The server
// declares `geometry: dict` (OpenAPI: {"type": "object"}) where TypeScript says
// `LineString`, and `kind: str` where TypeScript says `RouteKind`. Those
// divergences are deliberate — TS narrows what pydantic leaves loose — so a
// type diff would report failures that nobody should act on, and would train
// everyone to ignore the check.
//
// The drift that actually breaks a client is a field being added, removed, or
// renamed on one side only. That is exactly what a name-set comparison catches,
// and it is cheap enough to be trustworthy.
//
// Uses the TypeScript compiler API, which is already a devDependency — no new
// packages, and no regex parsing of TS source.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const TYPES_FILE = join(here, "..", "lib", "types.ts");

// OpenAPI schema name -> TypeScript interface name.
// Every schema in the document must appear here or in IGNORED; an unmapped new
// model is itself a failure, which is what stops a freshly added response body
// from going unmirrored.
const PAIRS = {
  LatLon: "LatLon",
  RouteRequest: "RouteRequest",
  UnsafeCounts: "UnsafeCounts",
  Segment: "Segment",
  UnsafePoint: "UnsafePoint",
  RouteAlternative: "RouteAlternative",
  RouteResponse: "RouteResponse",
  GeocodeResult: "GeocodeResult",
  // Names differ on purpose: the server calls it MetaResponse, the client
  // PackMeta.
  MetaResponse: "PackMeta",
};

const IGNORED = new Set([
  // FastAPI's built-in validation error shapes. The client only reads
  // `detail` as an opaque string (lib/api.ts), so mirroring them would be
  // noise.
  "HTTPValidationError",
  "ValidationError",
  // The client unwraps `.results` inline and works with GeocodeResult[]; there
  // is no wrapper interface to keep in sync.
  "GeocodeResponse",
]);

function tsInterfaces(file) {
  const source = ts.createSourceFile(
    file,
    readFileSync(file, "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  const out = {};
  for (const node of source.statements) {
    if (!ts.isInterfaceDeclaration(node)) continue;
    const props = {};
    for (const member of node.members) {
      if (!ts.isPropertySignature(member) || !member.name) continue;
      props[member.name.getText(source)] = member.questionToken !== undefined;
    }
    out[node.name.text] = props;
  }
  return out;
}

function openapiSchemas(doc) {
  const out = {};
  for (const [name, schema] of Object.entries(doc.components?.schemas ?? {})) {
    const required = new Set(schema.required ?? []);
    const props = {};
    for (const prop of Object.keys(schema.properties ?? {})) {
      props[prop] = !required.has(prop); // true = optional
    }
    out[name] = props;
  }
  return out;
}

const openapiPath = process.argv[2];
if (!openapiPath) {
  console.error("usage: node scripts/check-schema-sync.mjs <openapi.json>");
  process.exit(2);
}

const schemas = openapiSchemas(JSON.parse(readFileSync(openapiPath, "utf8")));
const interfaces = tsInterfaces(TYPES_FILE);
const problems = [];

for (const name of Object.keys(schemas)) {
  if (!(name in PAIRS) && !IGNORED.has(name)) {
    problems.push(
      `OpenAPI schema "${name}" is not mapped. Add it to web/lib/types.ts and ` +
        `to PAIRS in this script, or to IGNORED with a reason.`,
    );
  }
}

for (const [schemaName, interfaceName] of Object.entries(PAIRS)) {
  const schema = schemas[schemaName];
  if (!schema) {
    problems.push(
      `PAIRS maps "${schemaName}" but the API no longer exposes it — was it ` +
        `renamed or removed?`,
    );
    continue;
  }
  const iface = interfaces[interfaceName];
  if (!iface) {
    problems.push(
      `web/lib/types.ts has no interface "${interfaceName}" for API schema ` +
        `"${schemaName}".`,
    );
    continue;
  }

  const label =
    schemaName === interfaceName
      ? schemaName
      : `${schemaName}/${interfaceName}`;
  for (const prop of Object.keys(schema)) {
    if (!(prop in iface)) {
      problems.push(`${label}: API has "${prop}", TypeScript does not.`);
    }
  }
  for (const prop of Object.keys(iface)) {
    if (!(prop in schema)) {
      problems.push(`${label}: TypeScript has "${prop}", the API does not.`);
    }
  }
  for (const prop of Object.keys(schema)) {
    if (!(prop in iface)) continue;
    if (schema[prop] !== iface[prop]) {
      const apiSide = schema[prop] ? "optional" : "required";
      const tsSide = iface[prop] ? "optional" : "required";
      problems.push(
        `${label}.${prop}: API says ${apiSide}, TypeScript says ${tsSide}` +
          (tsSide === "required" ? ` (add "?")` : ` (remove "?")`),
      );
    }
  }
}

if (problems.length > 0) {
  console.error(
    "[schema-sync] web/lib/types.ts has drifted from api/schemas.py:\n",
  );
  for (const p of problems) console.error(`  - ${p}`);
  console.error(
    "\nThe POST /route contract is documented as frozen. If this change is " +
      "intentional, update web/lib/types.ts in the same commit.",
  );
  process.exit(1);
}

const checked = Object.keys(PAIRS).length;
console.log(`[schema-sync] ${checked} schemas match web/lib/types.ts`);
