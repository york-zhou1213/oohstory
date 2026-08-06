#!/usr/bin/env node
"use strict";

// Fetch one Linovelib page through the persistent visible Chrome service.
// Each request owns a new tab, so it cannot race with Shubaow's active tab.

const targetUrl = process.argv[2] || "";
const binary = process.env.OOHSTORY_LIBRARY_LINOVELIB_FETCH_BINARY === "1";
const cdpBase = (
  process.env.OOHSTORY_LIBRARY_LINOVELIB_CDP_URL || "http://127.0.0.1:9223"
).replace(/\/$/, "");
const authorizedHosts = new Set([
  "linovelib.com",
  "www.linovelib.com",
  "bilinovel.com",
  "www.bilinovel.com",
]);
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

let parsed;
try {
  parsed = new URL(targetUrl);
} catch {
  fail("invalid URL");
}
if (parsed.protocol !== "https:" || !authorizedHosts.has(parsed.hostname)) {
  fail("target is outside the configured Linovelib HTTPS hosts");
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.nextId = 0;
    this.pending = new Map();
    this.waiters = new Map();
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener(
        "error",
        () => reject(new Error("CDP WebSocket error")),
        { once: true },
      );
    });
    this.socket.addEventListener("message", (event) => {
      const payload = JSON.parse(String(event.data));
      if (payload.id && this.pending.has(payload.id)) {
        const { resolve, reject } = this.pending.get(payload.id);
        this.pending.delete(payload.id);
        if (payload.error) reject(new Error(payload.error.message || "CDP error"));
        else resolve(payload.result || {});
        return;
      }
      if (!payload.method || !this.waiters.has(payload.method)) return;
      const waiters = this.waiters.get(payload.method);
      this.waiters.delete(payload.method);
      for (const resolve of waiters) resolve(payload.params || {});
    });
  }

  async call(method, params = {}) {
    await this.opened;
    const id = ++this.nextId;
    return await new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  waitFor(method, timeoutMs) {
    return new Promise((resolve, reject) => {
      const done = (value) => {
        clearTimeout(timer);
        resolve(value);
      };
      const timer = setTimeout(() => {
        const waiters = this.waiters.get(method) || [];
        this.waiters.set(method, waiters.filter((item) => item !== done));
        reject(new Error(`CDP event ${method} timed out`));
      }, timeoutMs);
      const waiters = this.waiters.get(method) || [];
      waiters.push(done);
      this.waiters.set(method, waiters);
    });
  }

  close() {
    try { this.socket.close(); } catch {}
  }
}

async function newTab() {
  const response = await fetch(
    `${cdpBase}/json/new?${encodeURIComponent("about:blank")}`,
    { method: "PUT" },
  );
  if (!response.ok) throw new Error(`CDP tab creation failed: HTTP ${response.status}`);
  return await response.json();
}

function challengeState(value) {
  const sample = String(value || "").toLowerCase();
  return sample.includes("just a moment")
    || sample.includes("checking your browser")
    || sample.includes("challenge-platform")
    || sample.includes("cf-chl-");
}

async function navigate(client) {
  const loaded = client.waitFor("Page.loadEventFired", 45000).catch(() => null);
  const result = await client.call("Page.navigate", { url: targetUrl });
  if (result.errorText) throw new Error(result.errorText);
  // Advertising and analytics requests on older chapter pages can keep the
  // load event open long after the article DOM is readable.  Do not spend
  // 45 seconds waiting for unrelated subresources before inspecting it.
  await Promise.race([loaded, sleep(5000)]);
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const state = await client.call("Runtime.evaluate", {
      expression: `({
        url: location.href,
        title: document.title,
        text: (document.body && document.body.innerText || "").slice(0, 4000),
      })`,
      returnByValue: true,
    });
    const value = state.result && state.result.value || {};
    const host = new URL(value.url || targetUrl).hostname;
    if (!authorizedHosts.has(host)) throw new Error("browser redirected outside trusted hosts");
    if (!challengeState(`${value.title}\n${value.text}`)) return;
    await sleep(500);
  }
  throw new Error("Linovelib browser verification did not clear")
}

const timer = setTimeout(() => fail("Linovelib CDP fetch timed out"), 75000);
let tab;
let client;
try {
  tab = await newTab();
  client = new CdpClient(tab.webSocketDebuggerUrl);
  await client.call("Page.enable");
  await client.call("Runtime.enable");
  await navigate(client);
  await sleep(1200);
  let value;
  if (binary) {
    const result = await client.call("Runtime.evaluate", {
      expression: `(async () => {
        const response = await fetch(location.href, { credentials: "include" });
        const payload = new Uint8Array(await response.arrayBuffer());
        const chunks = [];
        for (let offset = 0; offset < payload.length; offset += 0x8000) {
          chunks.push(String.fromCharCode(...payload.subarray(offset, offset + 0x8000)));
        }
        return {
          status: response.status,
          url: response.url,
          contentType: response.headers.get("content-type") || "",
          body: "",
          bodyBase64: btoa(chunks.join("")),
        };
      })()`,
      awaitPromise: true,
      returnByValue: true,
    });
    value = result.result && result.result.value;
  } else {
    const result = await client.call("Runtime.evaluate", {
      expression: `({
        status: 200,
        url: location.href,
        contentType: document.contentType || "text/html",
        body: document.documentElement.outerHTML,
        bodyBase64: "",
      })`,
      returnByValue: true,
    });
    value = result.result && result.result.value;
  }
  if (!value || !authorizedHosts.has(new URL(value.url || targetUrl).hostname)) {
    throw new Error("Linovelib browser returned an invalid response");
  }
  process.stdout.write(JSON.stringify(value));
} catch (error) {
  fail(error && error.message || String(error));
} finally {
  clearTimeout(timer);
  if (client) client.close();
  if (tab && tab.id) {
    try { await fetch(`${cdpBase}/json/close/${tab.id}`); } catch {}
  }
}
