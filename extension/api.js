// Shared client for the local video-dl server.

const DEFAULTS = { serverUrl: "http://127.0.0.1:3210", token: "" };

export async function getSettings() {
  const s = await chrome.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...s };
}

export async function setSettings(patch) {
  await chrome.storage.local.set(patch);
}

async function request(path, { method = "GET", body } = {}) {
  const { serverUrl, token } = await getSettings();
  let res;
  try {
    res = await fetch(`${serverUrl}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Auth-Token": token,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("No se puede contactar al servidor. ¿Está corriendo?");
  }
  if (res.status === 401) {
    throw new Error("Token inválido. Revisalo en Opciones.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Error ${res.status}`);
  }
  return data;
}

export const api = {
  health: () => request("/health"),
  probe: (url, cookies) => request("/probe", { method: "POST", body: { url, cookies } }),
  download: (payload) => request("/download", { method: "POST", body: payload }),
  downloadPlaylist: (payload) => request("/download-playlist", { method: "POST", body: payload }),
  jobs: () => request("/jobs"),
  getSettings: () => request("/settings"),
  saveSettings: (patch) => request("/settings", { method: "POST", body: patch }),
  getUpload: () => request("/upload-settings"),
  saveUpload: (patch) => request("/upload-settings", { method: "POST", body: patch }),
  cancel: (id) => request(`/jobs/${id}/cancel`, { method: "POST" }),
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
