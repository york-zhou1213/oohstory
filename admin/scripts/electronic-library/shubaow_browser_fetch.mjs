#!/usr/bin/env node
"use strict";

// Fetch one owner-authorized shubaow.org page through a persistent, visible
// Chrome session. Cloudflare binds its clearance to the browser TLS and input
// fingerprint, so cookie replay and headless HTTP clients are insufficient.
// When Turnstile appears, this bridge captures the real viewport, maps the
// visible challenge iframe onto screenshot coordinates, and sends a genuine
// CDP mouse press/release to that point before continuing.

import { open, stat, unlink, writeFile } from "node:fs/promises";

const targetUrl = process.argv[2] || "";
const method = (process.argv[3] || "GET").toUpperCase();
const body = process.env.OOHSTORY_LIBRARY_SHUBAOW_FETCH_BODY || "";
const binary = process.env.OOHSTORY_LIBRARY_SHUBAOW_FETCH_BINARY === "1";
const cdpBase = (process.env.OOHSTORY_LIBRARY_SHUBAOW_CDP_URL || "http://127.0.0.1:9223").replace(/\/$/, "");
const screenshotPath = process.env.OOHSTORY_LIBRARY_SHUBAOW_CHALLENGE_SCREENSHOT
  || "/tmp/oohstory-shubaow-cloudflare-latest.png";
const challengeLockPath = process.env.OOHSTORY_LIBRARY_SHUBAOW_CHALLENGE_LOCK
  || "/tmp/oohstory-shubaow-cloudflare.lock";
const authorizedHosts = new Set(["www.shubaow.org", "shubaow.org", "pic.shubaow.org"]);
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
  fail("target is outside the authorized shubaow.org HTTPS hosts");
}
if (!["GET", "POST"].includes(method)) fail("unsupported method");

function hostnameOf(value) {
  try {
    return new URL(String(value || "")).hostname;
  } catch {
    return "";
  }
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.nextId = 0;
    this.pending = new Map();
    this.waiters = new Map();
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", () => reject(new Error("CDP WebSocket error")), { once: true });
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

  async call(methodName, params = {}) {
    await this.opened;
    const id = ++this.nextId;
    return await new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method: methodName, params }));
    });
  }

  waitFor(methodName, timeoutMs) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const waiters = this.waiters.get(methodName) || [];
        this.waiters.set(methodName, waiters.filter((item) => item !== done));
        reject(new Error(`CDP event ${methodName} timed out`));
      }, timeoutMs);
      const done = (value) => {
        clearTimeout(timer);
        resolve(value);
      };
      const waiters = this.waiters.get(methodName) || [];
      waiters.push(done);
      this.waiters.set(methodName, waiters);
    });
  }

  close() {
    try { this.socket.close(); } catch {}
  }
}

function isChallengeState(state) {
  const text = `${state.title || ""}\n${state.text || ""}`.toLowerCase();
  return text.includes("just a moment")
    || text.includes("performing security verification")
    || text.includes("checking your browser")
    || text.includes("请稍候")
    || text.includes("正在进行安全验证")
    || text.includes("验证您不是自动程序")
    || text.includes("cf-chl-");
}

async function pageState(client) {
  const result = await client.call("Runtime.evaluate", {
    expression: `({
      url: location.href,
      title: document.title,
      text: (document.body && document.body.innerText || "").slice(0, 3000),
    })`,
    returnByValue: true,
  });
  return result.result && result.result.value || {};
}

function flattenFrames(frameTree) {
  return [frameTree, ...(frameTree.childFrames || []).flatMap(flattenFrames)];
}

async function screenshotClickPoint(client, screenshot, pageTargetId) {
  const width = screenshot.readUInt32BE(16);
  const height = screenshot.readUInt32BE(20);
  const frameTree = await client.call("Page.getFrameTree");
  let challengeFrameId = flattenFrames(frameTree.frameTree).find(({ frame }) =>
    String(frame.url || "").includes("challenges.cloudflare.com"));
  challengeFrameId = challengeFrameId && challengeFrameId.frame.id;
  if (!challengeFrameId) {
    // Turnstile normally runs as an out-of-process iframe. Such an OOPIF is
    // present in /json/list with its page target as parent, but is omitted
    // from Page.getFrameTree on current Chromium builds.
    const targetsResponse = await fetch(`${cdpBase}/json/list`);
    const targets = targetsResponse.ok ? await targetsResponse.json() : [];
    const oopif = targets.find((item) =>
      item.type === "iframe"
      && item.parentId === pageTargetId
      && String(item.url || "").includes("challenges.cloudflare.com"));
    challengeFrameId = oopif && oopif.id;
  }
  if (!challengeFrameId) {
    throw new Error(`Cloudflare screenshot saved at ${screenshotPath}, but its visible iframe was not found`);
  }
  const owner = await client.call("DOM.getFrameOwner", { frameId: challengeFrameId });
  const node = owner.backendNodeId
    ? { backendNodeId: owner.backendNodeId }
    : { nodeId: owner.nodeId };
  const box = await client.call("DOM.getBoxModel", node);
  const quad = box.model && (box.model.border || box.model.content);
  if (!quad || quad.length !== 8) {
    throw new Error(`Cloudflare screenshot saved at ${screenshotPath}, but its iframe box is unavailable`);
  }
  const layout = await client.call("Page.getLayoutMetrics");
  const viewport = layout.cssVisualViewport || layout.visualViewport || {};
  const left = Math.min(quad[0], quad[2], quad[4], quad[6]) - Number(viewport.pageX || 0);
  const right = Math.max(quad[0], quad[2], quad[4], quad[6]) - Number(viewport.pageX || 0);
  const top = Math.min(quad[1], quad[3], quad[5], quad[7]) - Number(viewport.pageY || 0);
  const bottom = Math.max(quad[1], quad[3], quad[5], quad[7]) - Number(viewport.pageY || 0);
  const boxWidth = right - left;
  const boxHeight = bottom - top;
  // Turnstile's checkbox/spinner is 24px from the left edge of its 300x65 box.
  // These are viewport coordinates and therefore the same coordinates shown in
  // Page.captureScreenshot at the default device scale factor.
  const x = Math.round(left + Math.min(25, boxWidth / 2));
  const y = Math.round(top + boxHeight / 2);
  if (x < 0 || y < 0 || x >= width || y >= height) {
    throw new Error(`Cloudflare click point (${x},${y}) is outside screenshot ${width}x${height}`);
  }
  return { x, y };
}

async function captureAndClickChallenge(client, pageTargetId) {
  await client.call("Page.bringToFront");
  const shot = await client.call("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
  });
  const screenshot = Buffer.from(shot.data || "", "base64");
  if (screenshot.length < 24 || screenshot.toString("ascii", 1, 4) !== "PNG") {
    throw new Error("Cloudflare challenge screenshot capture failed");
  }
  await writeFile(screenshotPath, screenshot, { mode: 0o600 });
  const { x, y } = await screenshotClickPoint(client, screenshot, pageTargetId);
  await client.call("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await client.call("Input.dispatchMouseEvent", {
    type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1,
  });
  await client.call("Input.dispatchMouseEvent", {
    type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1,
  });
}

async function waitForClearance(client, timeoutMs = 35000) {
  const deadline = Date.now() + timeoutMs;
  let state = await pageState(client);
  while (Date.now() < deadline) {
    if (!isChallengeState(state) && /(^|\.)shubaow\.org$/i.test(hostnameOf(state.url))) {
      return state;
    }
    await sleep(500);
    state = await pageState(client);
  }
  throw new Error(`Cloudflare verification did not clear after screenshot click; evidence: ${screenshotPath}`);
}

async function acquireChallengeLock() {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    try {
      const handle = await open(challengeLockPath, "wx", 0o600);
      await handle.writeFile(`${process.pid}\n${Date.now()}\n`);
      return handle;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      try {
        const info = await stat(challengeLockPath);
        if (Date.now() - info.mtimeMs > 90000) await unlink(challengeLockPath);
      } catch (statError) {
        if (statError.code !== "ENOENT") throw statError;
      }
      await sleep(500);
    }
  }
  throw new Error("timed out waiting for the Cloudflare screenshot-click lock");
}

async function ensureClearance(tab, forceReload = false) {
  const client = new CdpClient(tab.webSocketDebuggerUrl);
  try {
    await client.call("Page.enable");
    await client.call("Runtime.enable");
    let state = await pageState(client);
    if (forceReload && /(^|\.)shubaow\.org$/i.test(hostnameOf(state.url))) {
      const loaded = client.waitFor("Page.loadEventFired", 30000).catch(() => null);
      await client.call("Page.reload", { ignoreCache: true });
      await loaded;
      state = await pageState(client);
    } else if (!/(^|\.)shubaow\.org$/i.test(hostnameOf(state.url))) {
      const loaded = client.waitFor("Page.loadEventFired", 30000).catch(() => null);
      await client.call("Page.navigate", { url: "https://www.shubaow.org/" });
      await loaded;
      state = await pageState(client);
    }
    if (!isChallengeState(state)) return;

    const lock = await acquireChallengeLock();
    try {
      state = await pageState(client);
      if (!isChallengeState(state)) return;
      await sleep(800);
      await captureAndClickChallenge(client, tab.id);
      await waitForClearance(client);
    } finally {
      await lock.close();
      try { await unlink(challengeLockPath); } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
  } finally {
    client.close();
  }
}

async function newTab(url = "about:blank") {
  const response = await fetch(`${cdpBase}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  if (!response.ok) throw new Error(`CDP tab creation failed: HTTP ${response.status}`);
  return await response.json();
}

async function browserFetch(tab) {
  const client = new CdpClient(tab.webSocketDebuggerUrl);
  try {
    await client.call("Page.enable");
    await client.call("Runtime.enable");
    if (method === "POST") {
      const loaded = client.waitFor("Page.loadEventFired", 60000);
      const fields = Array.from(new URLSearchParams(body).entries());
      await client.call("Runtime.evaluate", {
        expression: `(() => {
          const form = document.createElement("form");
          form.method = "POST";
          form.action = ${JSON.stringify(targetUrl)};
          for (const [name, value] of ${JSON.stringify(fields)}) {
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = name;
            input.value = value;
            form.appendChild(input);
          }
          document.body.appendChild(form);
          form.submit();
        })()`,
      });
      await loaded;
      await sleep(300);
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
      return result.result.value;
    }

    if (binary) {
      const loaded = client.waitFor("Page.loadEventFired", 60000).catch(() => null);
      await client.call("Page.navigate", { url: targetUrl });
      await loaded;
      const result = await client.call("Runtime.evaluate", {
        expression: `(async () => {
          const response = await fetch(location.href, { credentials: "include" });
          const payload = await response.arrayBuffer();
          const bytes = new Uint8Array(payload);
          const chunks = [];
          for (let offset = 0; offset < bytes.length; offset += 0x8000) {
            chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 0x8000)));
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
      return result.result.value;
    }

    const result = await client.call("Runtime.evaluate", {
      expression: `(async () => {
        const response = await fetch(${JSON.stringify(targetUrl)}, {
          method: "GET",
          credentials: "include",
          redirect: "follow",
          headers: { "Accept-Language": "zh-CN,zh;q=0.9" },
        });
        return {
          status: response.status,
          url: response.url,
          contentType: response.headers.get("content-type") || "",
          body: await response.text(),
          bodyBase64: "",
        };
      })()`,
      awaitPromise: true,
      returnByValue: true,
    });
    return result.result.value;
  } finally {
    client.close();
  }
}

const timer = setTimeout(() => fail("CDP browser fetch timed out"), 75000);
let requestTab;
try {
  const tabsResponse = await fetch(`${cdpBase}/json/list`);
  if (!tabsResponse.ok) throw new Error(`CDP unavailable: HTTP ${tabsResponse.status}`);
  const tabs = await tabsResponse.json();
  let sessionTab = tabs.find((item) =>
    item.type === "page" && /(^|\.)shubaow\.org$/i.test(hostnameOf(item.url)));
  if (!sessionTab) sessionTab = await newTab("https://www.shubaow.org/");
  await ensureClearance(sessionTab);

  requestTab = method === "POST" || binary ? await newTab() : sessionTab;
  let result = await browserFetch(requestTab);
  const challenged = () => result.status === 403
    || isChallengeState({ title: "", text: result.body });
  if (challenged()) {
    await ensureClearance(requestTab, requestTab === sessionTab);
    result = await browserFetch(requestTab);
  }
  if (challenged()) {
    throw new Error(`Cloudflare verification passed visually but ${targetUrl} still returned a challenge`);
  }
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  fail(error && error.message || String(error));
} finally {
  clearTimeout(timer);
  if (requestTab && (method === "POST" || binary) && requestTab.id) {
    try { await fetch(`${cdpBase}/json/close/${requestTab.id}`); } catch {}
  }
}
