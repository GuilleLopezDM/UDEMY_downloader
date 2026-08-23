import { api, getSettings, setSettings } from "./api.js";

const $ = (id) => document.getElementById(id);
const FIELDS = ["rate_limit_kbps", "gap_min", "gap_max", "video_sleep_min", "video_sleep_max"];

// --- conexión -------------------------------------------------------------

const stored = await getSettings();
$("serverUrl").value = stored.serverUrl;
$("token").value = stored.token;

$("save-conn").addEventListener("click", async () => {
  await setSettings({
    serverUrl: $("serverUrl").value.trim().replace(/\/$/, ""),
    token: $("token").value.trim(),
  });
  flash("saved-conn", "Guardado ✓");
  await loadThrottle();
});

// --- ritmo ----------------------------------------------------------------

function flash(id, text, isError = false) {
  const el = $(id);
  el.textContent = text;
  el.className = isError ? "err" : "saved";
  setTimeout(() => (el.textContent = ""), 2500);
}

function fillFields(t) {
  for (const f of FIELDS) $(f).value = t[f];
}

async function loadThrottle() {
  const box = $("presets");
  box.textContent = "";
  let data;
  try {
    data = await api.getSettings();
  } catch (e) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = `No se pudo leer la configuración: ${e.message}`;
    box.appendChild(p);
    return;
  }

  for (const [key, preset] of Object.entries(data.presets)) {
    const label = document.createElement("label");
    label.className = "preset";

    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "preset";
    radio.value = key;
    radio.checked = data.throttle.preset === key;
    radio.addEventListener("change", () => applyPreset(key));

    const text = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = (data.labels[key] || key).split(" — ")[0];
    const desc = document.createElement("span");
    desc.textContent = describe(preset);
    text.append(title, desc);

    label.append(radio, text);
    box.appendChild(label);
  }

  if (data.throttle.preset === "custom") {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "Actualmente estás usando un ajuste manual (ver abajo).";
    box.appendChild(p);
  }
  fillFields(data.throttle);
}

function describe(p) {
  const speed = p.rate_limit_kbps ? `${p.rate_limit_kbps} KB/s` : "sin límite de velocidad";
  const gap = p.gap_max ? `pausa ${p.gap_min}-${p.gap_max}s entre trabajos` : "sin pausa";
  const vid = p.video_sleep_max ? `, ${p.video_sleep_min}-${p.video_sleep_max}s entre videos` : "";
  return `${speed}, ${gap}${vid}`;
}

async function applyPreset(preset) {
  try {
    const { throttle } = await api.saveSettings({ preset });
    fillFields(throttle);
    flash("saved-throttle", "Aplicado ✓");
  } catch (e) {
    flash("saved-throttle", e.message, true);
  }
}

// Etiquetas para poder nombrar el campo que está mal, en vez de un "revisá los
// valores" que obliga a mirar los seis.
const NOMBRES = {
  rate_limit_kbps: "Velocidad máx.",
  gap_min: "Pausa entre trabajos (mínimo)",
  gap_max: "Pausa entre trabajos (máximo)",
  video_sleep_min: "Pausa entre videos (mínimo)",
  video_sleep_max: "Pausa entre videos (máximo)",
};

$("save-throttle").addEventListener("click", async () => {
  const patch = {};
  for (const f of FIELDS) {
    const crudo = $(f).value.trim();
    // `Number("")` es 0, así que un campo vacío guardaba `gap_min: 0,
    // gap_max: 0` y **apagaba el throttling entero, en silencio** — y quedaba
    // persistido en settings.json. Un campo vacío es un error, no un cero.
    if (crudo === "") {
      flash("saved-throttle", `Completá "${NOMBRES[f]}": vacío apagaría el freno`, true);
      return;
    }
    const valor = Number(crudo);
    if (!Number.isFinite(valor) || valor < 0) {
      flash("saved-throttle", `"${NOMBRES[f]}" tiene que ser un número ≥ 0`, true);
      return;
    }
    patch[f] = Math.round(valor);
  }
  if (patch.gap_max < patch.gap_min || patch.video_sleep_max < patch.video_sleep_min) {
    flash("saved-throttle", "El máximo no puede ser menor que el mínimo", true);
    return;
  }
  try {
    const { throttle } = await api.saveSettings(patch);
    fillFields(throttle);
    await loadThrottle();
    flash("saved-throttle", "Guardado ✓");
  } catch (e) {
    flash("saved-throttle", e.message, true);
  }
});

// --- subida ---------------------------------------------------------------

async function loadUpload() {
  const caja = $("rclone-estado");
  caja.textContent = "";
  let data;
  try {
    data = await api.getUpload();
  } catch (e) {
    caja.className = "estado falta";
    caja.textContent = `No se pudo leer la configuración: ${e.message}`;
    return;
  }

  // El estado real de rclone manda sobre lo que diga la config: tener "subir"
  // tildado sin rclone instalado sólo produce descargas que fallan al final.
  if (!data.rclone_available) {
    caja.className = "estado falta";
    caja.textContent =
      "rclone no está instalado. Instalalo con: winget install Rclone.Rclone " +
      "y después configurá Drive con: rclone config";
  } else if (!data.remotes.length) {
    caja.className = "estado falta";
    caja.textContent =
      "rclone está instalado pero no tiene remotos. Configurá Drive con: rclone config";
  } else {
    caja.className = "estado ok";
    caja.textContent = `rclone listo · remotos: ${data.remotes.join(", ")}`;
  }

  const select = $("up-remote");
  select.textContent = "";
  if (!data.remotes.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "(sin remotos configurados)";
    select.appendChild(opt);
  }
  for (const r of data.remotes) {
    const opt = document.createElement("option");
    opt.value = r;
    opt.textContent = r;
    opt.selected = r === data.upload.remote;
    select.appendChild(opt);
  }

  $("up-enabled").checked = data.upload.enabled;
  $("up-folder").value = data.upload.folder;
  $("up-delete").checked = data.upload.delete_after;
}

$("save-upload").addEventListener("click", async () => {
  const patch = {
    enabled: $("up-enabled").checked,
    remote: $("up-remote").value,
    folder: $("up-folder").value.trim(),
    delete_after: $("up-delete").checked,
  };
  if (patch.enabled && !patch.remote) {
    flash("saved-upload", "Elegí un remoto antes de activar la subida", true);
    return;
  }
  try {
    await api.saveUpload(patch);
    flash("saved-upload", "Guardado ✓");
    await loadUpload();
  } catch (e) {
    flash("saved-upload", e.message, true);
  }
});

await loadThrottle();
await loadUpload();
