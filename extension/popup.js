import { api, collectCookies } from "./api.js";

const $ = (id) => document.getElementById(id);
let currentUrl = "";
let pollTimer = null;

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentUrl = tab?.url || "";
  $("url").textContent = currentUrl || "Sin URL en esta pestaña";

  const usable = /^https?:/.test(currentUrl);
  $("download").disabled = !usable;
  $("probe").disabled = !usable;

  $("probe").addEventListener("click", onProbe);
  $("download").addEventListener("click", onDownload);
  $("download-course").addEventListener("click", onDownloadCourse);
  $("clear").addEventListener("click", onClear);
  $("options").addEventListener("click", (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });

  await checkHealth();
  await refreshJobs();
  pollTimer = setInterval(refreshJobs, 1000);
}

window.addEventListener("unload", () => clearInterval(pollTimer));

async function checkHealth() {
  try {
    await api.health();
    $("status").className = "dot online";
    $("status").title = "Servidor conectado";
  } catch {
    $("status").className = "dot offline";
    $("status").title = "Servidor no disponible";
    showMessage("El servidor local no responde. Levantalo con ./run.sh", "error");
  }
}

function showMessage(text, kind = "info") {
  const el = $("message");
  el.textContent = text;
  el.className = `message ${kind}`;
}

function hideMessage() {
  $("message").className = "message hidden";
}

// Devuelve las cookies, o revienta con un mensaje entendible.
//
// Antes esto atrapaba cualquier error y devolvía null: la petición salía SIN
// cookies mientras el check seguía tildado, así que un fallo de permisos era
// indistinguible de un sitio que no las necesita. En Udemy eso significa un 403
// sin ninguna pista de por qué.
async function cookiesIfWanted() {
  if (!$("cookies").checked) return null;

  let cookies;
  try {
    cookies = await collectCookies(currentUrl);
  } catch (e) {
    throw new Error(`No se pudieron leer las cookies: ${e.message}`);
  }

  if (!cookies.length) {
    throw new Error(
      "El navegador no devolvió ninguna cookie para este sitio. " +
        "¿Estás logueado en ESTE navegador? Si no las necesitás, destildá la opción.",
    );
  }
  return cookies;
}

async function onProbe() {
  hideMessage();
  $("probe").disabled = true;
  $("probe").textContent = "Analizando…";
  try {
    const info = await api.probe(currentUrl, await cookiesIfWanted());
    const box = $("probe-result");
    box.className = "probe";
    const boton = $("download-course");
    if (info.kind === "playlist") {
      box.textContent = `Curso: ${info.title} — ${info.count} clases`;
      // Recién acá sabemos que hay varias clases, así que el botón de curso
      // completo aparece sólo cuando tiene sentido.
      boton.textContent = `Descargar curso completo (${info.count})`;
      boton.className = "primary full";
    } else {
      const qualities = (info.formats || []).map((f) => `${f.height}p`).join(", ") || "n/d";
      box.textContent = `${info.title} · ${info.extractor} · ${qualities}`;
      boton.className = "primary full hidden";
    }
  } catch (e) {
    showMessage(e.message, "error");
  } finally {
    $("probe").disabled = false;
    $("probe").textContent = "Analizar";
  }
}

async function onDownload() {
  hideMessage();
  $("download").disabled = true;
  try {
    await api.download({
      url: currentUrl,
      cookies: await cookiesIfWanted(),
      quality: $("quality").value,
      subtitles: $("subtitles").checked,
      audio_only: $("audio").checked,
    });
    showMessage("Descarga encolada.", "info");
    await refreshJobs();
  } catch (e) {
    showMessage(e.message, "error");
  } finally {
    $("download").disabled = false;
  }
}

async function onDownloadCourse() {
  hideMessage();
  const boton = $("download-course");
  const etiqueta = boton.textContent;
  boton.disabled = true;
  // Enumerar el curso son varias llamadas a la API del sitio: puede tardar
  // bastante y sin este aviso el popup parece colgado.
  boton.textContent = "Enumerando el curso…";
  try {
    const r = await api.downloadPlaylist({
      url: currentUrl,
      cookies: await cookiesIfWanted(),
      quality: $("quality").value,
      subtitles: $("subtitles").checked,
      audio_only: $("audio").checked,
    });
    const omitidas = r.skipped ? ` (${r.skipped} omitidas sin URL)` : "";
    const destino = r.folder ? ` → carpeta "${r.folder}"` : "";
    showMessage(`${r.count} clases en cola${omitidas}${destino}. Se bajan de a una.`, "info");
    await refreshJobs();
  } catch (e) {
    showMessage(e.message, "error");
  } finally {
    boton.disabled = false;
    boton.textContent = etiqueta;
  }
}

async function onClear() {
  try {
    await api.clear();
    await refreshJobs();
  } catch (e) {
    showMessage(e.message, "error");
  }
}

const LABELS = {
  queued: "En cola",
  waiting: "Esperando turno",
  running: "Descargando",
  postprocessing: "Procesando",
  uploading: "Subiendo",
  done: "Listo",
  error: "Error",
  // Se distingue de "Error" a propósito: el video está bajado y en disco, lo
  // único que falló es la subida. Decir "Error" a secas haría pensar que no
  // quedó nada.
  upload_error: "Bajado, sin subir",
  cancelled: "Cancelado",
};

async function refreshJobs() {
  let jobs;
  try {
    ({ jobs } = await api.jobs());
    $("status").className = "dot online";
  } catch {
    $("status").className = "dot offline";
    return;
  }

  const list = $("job-list");
  list.textContent = "";
  if (!jobs.length) {
    const li = document.createElement("li");
    li.className = "job-meta";
    li.textContent = "Todavía no hay descargas.";
    list.appendChild(li);
    return;
  }

  // En orden de encolado, NO al revés: para un curso ese es el orden de las
  // clases. Mostrarlo invertido dejaba la clase 40 arriba y la 1 abajo.
  for (const job of jobs) {
    const li = document.createElement("li");

    const title = document.createElement("div");
    title.className = "job-title";
    if (job.position) {
      const num = document.createElement("span");
      num.className = "job-num";
      num.textContent = `${job.position}/${job.total}`;
      title.appendChild(num);
    }
    title.appendChild(document.createTextNode(job.title || job.url));
    title.title = job.url;
    li.appendChild(title);

    const bar = document.createElement("div");
    const fallo = job.status === "error" || job.status === "upload_error";
    bar.className = `bar ${job.status === "done" ? "done" : fallo ? "error" : ""}`;
    const fill = document.createElement("span");
    fill.style.width = `${job.status === "done" ? 100 : job.percent}%`;
    bar.appendChild(fill);
    li.appendChild(bar);

    const meta = document.createElement("div");
    meta.className = "job-meta";
    const left = document.createElement("span");
    left.textContent = LABELS[job.status] || job.status;
    const right = document.createElement("span");
    if (job.status === "running") {
      right.textContent = [job.speed, job.eta != null ? `${job.eta}s` : null]
        .filter(Boolean)
        .join(" · ");
    } else if (job.status === "waiting" && job.wait_seconds != null) {
      right.textContent = `arranca en ${job.wait_seconds}s`;
    } else if (job.status === "uploading") {
      right.textContent = `${job.percent}%`;
    }
    meta.append(left, right);
    li.appendChild(meta);

    if (job.error) {
      const err = document.createElement("div");
      err.className = "job-error";
      err.textContent = job.error;
      li.appendChild(err);
    }

    if (["queued", "waiting", "running", "postprocessing", "uploading"].includes(job.status)) {
      const cancel = document.createElement("button");
      cancel.className = "link";
      cancel.textContent = "Cancelar";
      cancel.addEventListener("click", async () => {
        try {
          await api.cancel(job.id);
          await refreshJobs();
        } catch (e) {
          showMessage(e.message, "error");
        }
      });
      li.appendChild(cancel);
    }

    list.appendChild(li);
  }
}

init();
