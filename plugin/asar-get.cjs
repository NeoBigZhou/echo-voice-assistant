// asar-get.cjs - locate a packed package's entry file inside an app.asar archive.
//
// Plain Node cannot read inside app.asar; this helper only reads the JSON file
// table (a plain file region) and computes the virtual path. The caller must run
// under Electron's Node (ELECTRON_RUN_AS_NODE=1) so that importing that path works.
//
//   const get = require("./asar-get.cjs");
//   const entry = get("C:\\...\\resources\\app.asar", "@deepseek-ai/dsh-app-boot");
//
// Deployed next to the echo-host plugin by install-echo-host-plugin.ps1.

const fs = require("node:fs");
const path = require("node:path");

function readHeader(asarPath) {
  const fd = fs.openSync(asarPath, "r");
  try {
    const head = Buffer.alloc(16);
    fs.readSync(fd, head, 0, 16, 0);
    if (head.readUInt32LE(0) !== 4) throw new Error("not an asar archive: " + asarPath);
    const headerSize = head.readUInt32LE(12);
    const jsonBuf = Buffer.alloc(headerSize);
    fs.readSync(fd, jsonBuf, 0, headerSize, 16);
    return JSON.parse(jsonBuf.toString("utf8"));
  } finally {
    fs.closeSync(fd);
  }
}

function resolveEntry(header, packageName) {
  let node = header;
  for (const part of packageName.split("/")) {
    node = node && node.files && node.files[part];
    if (!node) return undefined;
  }
  if (!node || !node.files) return undefined;

  const dir = "node_modules/" + packageName;
  // The manifest body is not needed for path computation: DSH packages keep the
  // entry at one of these conventional locations.
  const candidates = [["lib", "index.js"], ["lib", "bin.js"], ["index.js"], ["bin.js"]];
  for (const parts of candidates) {
    let cursor = node;
    for (const part of parts) {
      cursor = cursor && cursor.files && cursor.files[part];
      if (!cursor) break;
    }
    if (cursor) return dir + "/" + parts.join("/");
  }
  return undefined;
}

/**
 * @param {string} asarPath absolute path of app.asar
 * @param {string} packageName npm package name (e.g. "@deepseek-ai/dsh-app-boot")
 * @returns {string|undefined} virtual path of the package entry inside the archive
 */
function asarGet(asarPath, packageName) {
  const header = readHeader(asarPath);
  return resolveEntry(header, packageName);
}

module.exports = asarGet;
module.exports.readHeader = readHeader;
