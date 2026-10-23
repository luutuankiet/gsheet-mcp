// Copied from google-docs-mcp@2fb4652 src/<package>/sandbox/prelude.js; the doc model and style writers dropped.
// Runtime for a sandboxed script. Evaluated once per worker before the script.
// Defines the console capture, the op recorder and the __start/__poll driver;
// the tool that runs scripts adds its helpers to `helpers`. Helpers only
// record operations, the server turns them into API requests, so a script
// never calls Google itself.
"use strict";

// mini-racer installs timer globals as non-configurable but writable.
for (const name of ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "queueMicrotask"]) {
  if (name in globalThis) globalThis[name] = undefined;
}

const __S = {
  ops: [],
  logs: [],
  logBytes: 0,
  status: "idle",       // idle | running | done | error
  result: undefined,
  error: null,
  maxOps: 5000,
};
globalThis.__S = __S;

globalThis.console = (() => {
  const log = (...args) => {
    if (__S.logs.length >= 100 || __S.logBytes > 8192) return;
    const line = args.map(a => typeof a === "string" ? a : __safeJSON(a)).join(" ");
    __S.logBytes += line.length;
    __S.logs.push(line.slice(0, 1000));
  };
  return { log, info: log, warn: log, error: log, debug: log };
})();

function __safeJSON(v) {
  try { return JSON.stringify(v); } catch (e) { return String(v); }
}

function __push(op) {
  if (__S.ops.length >= __S.maxOps) {
    throw new RangeError(`a script records at most ${__S.maxOps} operations`);
  }
  __S.ops.push(op);
}
globalThis.__push = __push;

const helpers = {};
globalThis.helpers = helpers;

// ---- driver -------------------------------------------------------------------

function __start(inputJSON, modelJSON, maxOps) {
  __S.maxOps = maxOps;
  globalThis.input = JSON.parse(inputJSON);
  globalThis.model = JSON.parse(modelJSON);
  __S.status = "running";
  __main(globalThis.input).then(
    (v) => { __S.result = v; __S.status = "done"; },
    (e) => { __S.error = e; __S.status = "error"; },
  );
}

function __poll() {
  const out = { status: __S.status, logs: __S.logs.splice(0) };
  if (__S.status === "done") {
    out.ops = __S.ops;
    try {
      const s = JSON.stringify(__S.result === undefined ? null : __S.result, (k, v) => {
        if (typeof v === "bigint") throw new TypeError("return value contains a BigInt, which JSON cannot represent");
        if (typeof v === "function") throw new TypeError(`return value contains a function${k ? ` at key "${k}"` : ""}, which JSON cannot represent`);
        if (typeof v === "symbol") throw new TypeError("return value contains a Symbol, which JSON cannot represent");
        return v;
      });
      out.result = s === undefined ? "null" : s;
    } catch (e) {
      out.status = "error";
      out.error = { name: e.name, message: `return value is not JSON-serialisable: ${e.message}`, stack: "" };
    }
  }
  if (__S.status === "error") {
    const e = __S.error;
    out.error = (e && typeof e === "object")
      ? { name: e.name || "Error", message: String(e.message), stack: String(e.stack || "") }
      : { name: "Error", message: `script threw a non-Error value: ${__safeJSON(e)}`, stack: "" };
  }
  return JSON.stringify(out);
}
