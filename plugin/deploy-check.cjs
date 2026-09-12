// deploy-check.cjs - read-only verification of the echo-host registration.
//
// Verifies the registration the way DSH Desktop 2.0.9 actually composes it: the
// desktop profile's own patch layer (<profile>\cordis.patch.yml), applied after
// every bundle layer and handed to boot() as `patches`. This parses that file
// with DSH's own patch parser, composes the entry list, and imports the plugin
// entry the row points at.
//
// Must run under Electron's Node (asar support) to import app.asar code:
//   ELECTRON_RUN_AS_NODE=1 "DSH Desktop.exe" deploy-check.cjs <resourcesDir> [profilePatchFile]
//
// Exit codes: 0 ok | 1 unexpected | 2 patch parse failed | 3 row missing/invalid
//             4 entry file missing | 5 plugin import failed | 6 no apply export

const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL, fileURLToPath } = require("node:url");

const resourcesDir = process.argv[2];
if (!resourcesDir) { console.log("  [FAIL] usage: deploy-check.cjs <resourcesDir> [profilePatchFile]"); process.exit(1); }

const installRoot = resourcesDir;
const asarPath = path.join(resourcesDir, "app.asar");
const profilePatch = process.argv[3] ||
  path.join(process.env.USERPROFILE || process.env.HOME || "", ".dsh", "profiles", "desktop", "cordis.patch.yml");

const warn = (fmt, ...rest) =>
  console.log("  [warn] " + fmt.replace(/%C/g, "%s") + " " + rest.map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v))).join(" "));

function resolvePackedEntry(packageName) {
  // Electron's asar-aware require.resolve finds app.asar/node_modules; the file
  // table probe is the plain-Node fallback.
  try {
    return require.resolve(packageName, { paths: [asarPath, installRoot, __dirname] });
  } catch { /* fall through */ }
  try {
    const get = require(path.join(__dirname, "asar-get.cjs"));
    return get(asarPath, packageName);
  } catch { /* unreachable under Electron */ }
  return undefined;
}

(async () => {
  const bootEntry = resolvePackedEntry("@deepseek-ai/dsh-app-boot");
  if (!bootEntry) { console.log("  [FAIL] cannot locate @deepseek-ai/dsh-app-boot inside app.asar"); process.exit(1); }
  const boot = await import(pathToFileURL(bootEntry).href);
  const api = boot.default ?? boot;
  for (const fn of ["composeEntries", "loadOverlayPatches"]) {
    if (typeof api[fn] !== "function") { console.log("  [FAIL] dsh-app-boot exposes no " + fn); process.exit(1); }
  }

  if (!fs.existsSync(profilePatch)) {
    console.log("  [FAIL] profile patch layer not found: " + profilePatch);
    process.exit(2);
  }

  // DSH's own parser: a malformed patch file throws here instead of at boot.
  const warnings = [];
  let patches;
  try {
    patches = api.loadOverlayPatches("dsh-plugin-desktop", profilePatch);
  } catch (cause) {
    console.log("  [FAIL] profile patch parse failed: " + String(cause && cause.message));
    process.exit(2);
  }
  console.log("  [OK] profile patch parses: " + profilePatch);

  const tree = api.composeEntries(patches, (message) => warnings.push(message));
  for (const message of warnings) console.log("  [warn] " + message);

  const list = Array.isArray(tree) ? tree : (tree && (tree.entries || tree.data)) || [];
  const row = list.find((r) => r && r.id === "echo-host");
  if (!row) { console.log("  [FAIL] echo-host row is absent from the composed patch list"); process.exit(3); }
  console.log("  [OK] echo-host row present, name=" + row.name);

  const name = String(row.name);
  const target = name.startsWith("file:") ? fileURLToPath(name) : name;
  let entryPath = target;
  if (/^[A-Za-z]:[\\/]/.test(target) || target.startsWith("/") || target.startsWith("\\\\")) {
    const exists = fs.existsSync(target);
    console.log("  [" + (exists ? "OK" : "FAIL") + "] plugin entry " + (exists ? "exists" : "MISSING") + ": " + target);
    if (!exists) process.exit(4);
  } else {
    console.log("  [INFO] entry is a bare specifier resolved at runtime: " + target);
    entryPath = resolvePackedEntry(target.split("/")[0].startsWith("@") ? target.split("/").slice(0, 2).join("/") : target.split("/")[0]);
    if (!entryPath) { console.log("  [FAIL] cannot resolve bare entry " + target); process.exit(4); }
  }

  // Load the module: proves it parses/links as ESM and exports apply(ctx).
  let mod;
  try {
    mod = await import(pathToFileURL(entryPath).href);
  } catch (cause) {
    console.log("  [FAIL] plugin import failed: " + String(cause && cause.message));
    process.exit(5);
  }
  const plugin = mod.default ?? mod;
  console.log("  [OK] plugin import ok, name=" + plugin.name + " build=" + (plugin.ECHO_HOST_BUILD ?? "?") +
    " apply=" + typeof plugin.apply);
  if (typeof plugin.apply !== "function") { console.log("  [FAIL] plugin exports no apply(ctx)"); process.exit(6); }
})().catch((cause) => {
  console.log("  [FAIL] check crashed: " + (cause && cause.stack ? cause.stack : String(cause)));
  process.exit(1);
});
