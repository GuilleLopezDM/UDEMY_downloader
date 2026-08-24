// Shared client for the local video-dl server.

const DEFAULTS = { serverUrl: "http://127.0.0.1:3210", token: "" };

// Red de seguridad, no un límite de producto: un fetch sin `signal` se queda
// colgado para siempre si el servidor acepta la conexión y no contesta nunca, y
// el popup deja los botones deshabilitados sin decir nada. Enumerar un curso
// grande son minutos por diseño, así que ese camino lleva un techo aparte.
const TIMEOUT_MS = 30_000;
const TIMEOUT_LARGO_MS = 15 * 60_000;

// Se lanza cuando el token es inválido, para poder distinguirlo de "el servidor
// no está". Antes los dos llegaban a la UI como el mismo `catch` y un token mal
// pegado se veía como un servidor caído: punto rojo y ninguna pista.
export class ErrorDeToken extends Error {
  constructor() {
    super("Token inválido. Revisalo en Opciones.");
    this.name = "ErrorDeToken";
  }
}

export async function getSettings() {
  const s = await chrome.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...s };
}

export async function setSettings(patch) {
  await chrome.storage.local.set(patch);
}

async function request(path, { method = "GET", body, timeout = TIMEOUT_MS, auth = true } = {}) {
  const { serverUrl, token } = await getSettings();
  const headers = { "Content-Type": "application/json" };
  // `/health` es público a propósito (app.py): mandarle el token era gratis
  // pero innecesario, y el principio es no pasear la credencial de más.
  if (auth) headers["X-Auth-Token"] = token;

  let res;
  try {
    res = await fetch(`${serverUrl}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeout),
    });
  } catch (e) {
    if (e?.name === "TimeoutError") {
      throw new Error("El servidor no respondió a tiempo.");
    }
    throw new Error("No se puede contactar al servidor. ¿Está corriendo?");
  }
  if (res.status === 401) {
    throw new ErrorDeToken();
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Error ${res.status}`);
  }
  return data;
}

export const api = {
  health: () => request("/health", { auth: false }),
  paths: () => request("/paths"),
  probe: (url, cookies) =>
    request("/probe", { method: "POST", body: { url, cookies }, timeout: TIMEOUT_LARGO_MS }),
  diagnose: (url, cookies) =>
    request("/diagnose", { method: "POST", body: { url, cookies }, timeout: TIMEOUT_LARGO_MS }),
  download: (payload) => request("/download", { method: "POST", body: payload }),
  downloadPlaylist: (payload) =>
    request("/download-playlist", { method: "POST", body: payload, timeout: TIMEOUT_LARGO_MS }),
  jobs: () => request("/jobs"),
  getSettings: () => request("/settings"),
  saveSettings: (patch) => request("/settings", { method: "POST", body: patch }),
  getUpload: () => request("/upload-settings"),
  saveUpload: (patch) => request("/upload-settings", { method: "POST", body: patch }),
  cancel: (id) => request(`/jobs/${id}/cancel`, { method: "POST" }),
  cancelAll: () => request("/jobs/cancel-all", { method: "POST" }),
  refreshCookies: (cookies) =>
    request("/jobs/refresh-cookies", { method: "POST", body: { cookies } }),
  clear: () => request("/jobs/clear", { method: "POST" }),
};

const shapeCookie = (c) => ({
  name: c.name,
  value: c.value,
  domain: c.domain,
  path: c.path,
  secure: c.secure,
  hostOnly: c.hostOnly,
  expirationDate: c.expirationDate ?? null,
});

// Cookies aplicables a `url`, y solo esas.
//
// Antes se calculaba el "dominio registrable" cortando el hostname por
// `slice(-2)`, lo que en un `cursos.example.co.uk` daba `co.uk` y pedía las
// cookies de TODOS los sitios .co.uk. Preguntar por `url` deja que el navegador
// aplique su propio match de dominio, path y `secure`: devuelve exactamente lo
// que el sitio recibiría, sin sobrar ni faltar.
export async function collectCookies(url) {
  const unpartitioned = await chrome.cookies.getAll({ url });

  // Las cookies particionadas (CHIPS) no vienen en la consulta normal, y Brave
  // particiona más agresivo que Chrome. Se piden aparte; la API es reciente, así
  // que si el navegador no la soporta seguimos con las que ya tenemos.
  let partitioned = [];
  try {
    const { origin } = new URL(url);
    partitioned = await chrome.cookies.getAll({
      url,
      partitionKey: { topLevelSite: origin },
    });
  } catch {
    partitioned = [];
  }

  const porClave = new Map();
  for (const c of [...unpartitioned, ...partitioned]) {
    porClave.set(`${c.domain}|${c.path}|${c.name}`, c);
  }
  return [...porClave.values()].map(shapeCookie);
}
