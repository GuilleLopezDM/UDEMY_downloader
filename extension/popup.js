import { api, collectCookies, ErrorDeToken } from "./api.js";

const $ = (id) => document.getElementById(id);

// La URL sale del campo editable, no de una variable fija. Udemy redirige la
// página del curso a la última clase vista, así que la pestaña activa muchas
// veces NO tiene la URL que uno quiere encolar. El campo se precarga con la de
// la pestaña y se puede pegar otra encima.
const urlActual = () => $("url").value.trim();

const EN_WINDOWS = /Windows/i.test(navigator.userAgent);
const COMO_LEVANTARLO = EN_WINDOWS
  ? "powershell -ExecutionPolicy Bypass -File .\\run.ps1"
  : "./run.sh";

let pollTimer = null;

function pintarPin(fijada) {
  const b = $("pin");
  b.textContent = fijada ? "Fijada ✓" : "Fijar";
  b.className = fijada ? "secondary pin activa" : "secondary pin";
  b.title = fijada
    ? "Quitar: al abrir el popup vuelve a usarse la URL de la pestaña"
    : "Fijar esta URL para las próximas veces";
}

async function onPin() {
  const { pinnedUrl } = await chrome.storage.local.get({ pinnedUrl: "" });
  if (pinnedUrl) {
    await chrome.storage.local.set({ pinnedUrl: "" });
    pintarPin(false);
    showMessage("URL desfijada. La próxima vez se usa la de la pestaña.", "info");
    return;
  }
  const u = urlActual();
  if (!/^https?:/.test(u)) {
    showMessage("Escribí una URL válida antes de fijarla.", "error");
    return;
  }
  await chrome.storage.local.set({ pinnedUrl: u });
  pintarPin(true);
  showMessage("URL fijada. Se va a cargar sola al abrir el popup.", "info");
}

// La URL con la que se hizo el último Analizar. El botón de curso completo sólo
// vale para ESA: antes sólo se escondía cuando la URL dejaba de ser válida, así
// que pegar otro curso encima dejaba el botón con el conteo del anterior y
// encolaba el curso equivocado sin avisar.
let urlAnalizada = null;

function ocultarAnalisis() {
  urlAnalizada = null;
  $("diagnose").className = "link hidden";
  $("diagnose-result").className = "diag hidden";
  $("download-course").className = "primary full hidden";
  $("probe-result").className = "probe hidden";
  $("probe-result").textContent = "";
}

function actualizarBotones() {
  const usable = /^https?:/.test(urlActual());
  $("download").disabled = !usable;
  $("probe").disabled = !usable;
  if (urlAnalizada !== null && urlActual() !== urlAnalizada) ocultarAnalisis();
  if (!usable) ocultarAnalisis();
}

async function init() {
  // Los listeners se registran ANTES de cualquier await: si `chrome.tabs.query`
  // o `storage` fallan, el popup queda con la UI viva y un mensaje de error, en
  // vez de un formulario mudo donde ningún botón hace nada.
  $("url").addEventListener("input", actualizarBotones);
  $("pin").addEventListener("click", onPin);
  $("probe").addEventListener("click", onProbe);
  $("download").addEventListener("click", onDownload);
  $("download-course").addEventListener("click", onDownloadCourse);
  $("clear").addEventListener("click", onClear);
  $("cancel-all").addEventListener("click", onCancelAll);
  $("refresh-session").addEventListener("click", onRefreshSession);
  $("diagnose").addEventListener("click", onDiagnose);
  $("options").addEventListener("click", (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    // Una URL fijada gana sobre la de la pestaña: el caso de uso es un curso de
    // Udemy, y Udemy redirige la página del curso a la última clase vista, así
    // que la pestaña casi nunca tiene la URL que uno quiere encolar.
    const { pinnedUrl } = await chrome.storage.local.get({ pinnedUrl: "" });
    $("url").value = pinnedUrl || tab?.url || "";
    pintarPin(Boolean(pinnedUrl));
  } catch (e) {
    showMessage(`No se pudo leer la pestaña actual: ${e.message}`, "error");
  }
  actualizarBotones();

  await checkHealth();
  await refreshJobs();
  await mostrarRuta();
  pollTimer = setInterval(refreshJobs, 1000);
}

window.addEventListener("unload", () => clearInterval(pollTimer));

function marcarEstado(online, detalle) {
  const el = $("status");
  el.className = online ? "dot online" : "dot offline";
  el.title = detalle;
  el.setAttribute("aria-label", detalle);
}

async function checkHealth() {
  try {
    await api.health();
    marcarEstado(true, "Servidor conectado");
  } catch {
    marcarEstado(false, "Servidor no disponible");
    showMessage(`El servidor local no responde. Levantalo con: ${COMO_LEVANTARLO}`, "error");
  }
}

// La carpeta de descargas vive detrás del token (filtra el nombre del usuario),
// así que se pide aparte y en silencio: si falla, simplemente no se muestra.
async function mostrarRuta() {
  try {
    const { download_dir, espacio_libre_gb } = await api.paths();
    const libre = espacio_libre_gb != null ? ` · ${espacio_libre_gb} GB libres` : "";
    $("ruta").textContent = `${download_dir}${libre}`;
    $("ruta").title = `${download_dir}${libre}`;
  } catch {
    $("ruta").textContent = "";
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
async function cookiesIfWanted(url = urlActual()) {
  if (!$("cookies").checked) return null;

  let cookies;
  try {
    cookies = await collectCookies(url);
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

function opcionesActuales() {
  return {
    quality: $("quality").value,
    subtitles: $("subtitles").checked,
    audio_only: $("audio").checked,
  };
}

async function onProbe() {
  hideMessage();
  $("probe").disabled = true;
  $("probe").textContent = "Analizando…";
  const url = urlActual();
  try {
    const info = await api.probe(url, await cookiesIfWanted(url));
    const box = $("probe-result");
    box.className = "probe";
    const boton = $("download-course");
    urlAnalizada = url;
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
    // El botón de diagnóstico aparece sólo cuando hubo un fallo: es lo único
    // que distingue "no tenés acceso" de "faltan headers" o "host equivocado".
    $("diagnose").className = "link";
  } finally {
    $("probe").textContent = "Analizar";
    actualizarBotones();
  }
}

async function onDiagnose() {
  const caja = $("diagnose-result");
  const boton = $("diagnose");
  boton.disabled = true;
  boton.textContent = "Probando…";
  caja.className = "diag";
  caja.textContent = "Probando la página y la API, con y sin sesión…";
  try {
    const url = urlActual();
    const r = await api.diagnose(url, await cookiesIfWanted(url));
    const filas = Object.entries(r.pruebas).map(
      ([n, p]) => `${String(p.estado ?? "—").padEnd(4)} ${n}`,
    );
    const tokens = (r.tokens || [])
      .map((t) => `  ${t.nombre} @ ${t.dominio}${t.host_only ? " (host-only)" : ""} → ${t.huella}`)
      .join("\n");
    caja.textContent =
      `${filas.join("\n")}\n\ntokens:\n${tokens || "  (ninguno)"}\n\n${r.lectura}`;
  } catch (e) {
    caja.textContent = `No se pudo diagnosticar: ${e.message}`;
  } finally {
    boton.disabled = false;
    boton.textContent = "¿Por qué falla? Diagnosticar";
  }
}

async function onDownload() {
  hideMessage();
  $("download").disabled = true;
  try {
    await api.download({
      url: urlActual(),
      cookies: await cookiesIfWanted(),
      ...opcionesActuales(),
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
      url: urlActual(),
      cookies: await cookiesIfWanted(),
      ...opcionesActuales(),
    });
    const omitidas = r.skipped ? ` (${r.skipped} omitidas sin URL usable)` : "";
    const destino = r.folder ? ` → carpeta "${r.folder}"` : "";
    const libre = r.espacio_libre_gb != null ? ` Quedan ${r.espacio_libre_gb} GB libres.` : "";
    showMessage(
      `${r.count} clases en cola${omitidas}${destino}. Se bajan de a una.${libre}`,
      "info",
    );
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

async function onCancelAll() {
  try {
    const { cancelled } = await api.cancelAll();
    showMessage(`${cancelled} trabajo(s) cancelado(s).`, "info");
    await refreshJobs();
  } catch (e) {
    showMessage(e.message, "error");
  }
}

// La sesión de la cola se refresca sola desde el service worker mientras hay
// trabajos pendientes. Este botón es el control manual: sirve cuando acabás de
// volver a loguearte en Udemy y no querés esperar al próximo ciclo.
async function onRefreshSession() {
  const boton = $("refresh-session");
  boton.disabled = true;
  try {
    const url = sesionActual?.url || urlActual();
    const cookies = await collectCookies(url);
    if (!cookies.length) {
      showMessage("El navegador no devolvió cookies para ese sitio. ¿Seguís logueado?", "error");
      return;
    }
    const { actualizados } = await api.refreshCookies(cookies);
    showMessage(
      actualizados
        ? `Sesión actualizada en ${actualizados} clase(s) pendiente(s).`
        : "No hay clases pendientes que necesiten la sesión.",
      "info",
    );
    await refreshJobs();
  } catch (e) {
    showMessage(e.message, "error");
  } finally {
    boton.disabled = false;
  }
}

function pintarSesion(sesion) {
  sesionActual = sesion;
  const boton = $("refresh-session");
  if (!sesion?.pendientes || !sesion.url) {
    boton.className = "link hidden";
    return;
  }
  boton.className = "link";
  if (sesion.vence_en == null) {
    boton.textContent = "Actualizar sesión";
    boton.title = "Cookies de sesión, sin fecha de vencimiento conocida";
    return;
  }
  const minutos = Math.round((sesion.vence_en * 1000 - Date.now()) / 60000);
  if (minutos <= 0) {
    boton.textContent = "Sesión vencida — actualizar";
    boton.title = "Las clases que faltan van a fallar con 403 hasta que la actualices";
  } else {
    const restante = minutos < 90 ? `${minutos} min` : `${Math.round(minutos / 60)} h`;
    boton.textContent = "Actualizar sesión";
    boton.title = `La sesión de la cola vence en ${restante}. Se refresca sola, o forzalo acá.`;
  }
}

let sesionActual = null;

const LABELS = {
  queued: "En cola",
  waiting: "Esperando turno",
  running: "Descargando",
  postprocessing: "Procesando",
  uploading: "Subiendo",
  done: "Listo",
  // No es "Listo": no se bajó nada ahora. El registro de descargados dice que
  // ya la tenías, y si borraste el archivo igual la saltea.
  skipped: "Ya estaba descargada",
  error: "Error",
  // Se distingue de "Error" a propósito: el video está bajado y en disco, lo
  // único que falló es la subida. Decir "Error" a secas haría pensar que no
  // quedó nada.
  upload_error: "Bajado, sin subir",
  cancelled: "Cancelado",
};

const ACTIVOS = ["queued", "waiting", "running", "postprocessing", "uploading"];
const REINTENTABLES = ["error", "upload_error", "cancelled", "skipped"];

// Filas vivas, por id de job.
//
// La lista se repintaba entera cada segundo (`list.textContent = ""`), y con 200
// clases eso hacía tres cosas: el scroll volvía al tope —imposible mirar la
// clase 150—, el foco de teclado caía a <body>, y un click cuyo mousedown y
// mouseup caían a los dos lados de un repintado no llegaba a emitirse nunca, así
// que Cancelar fallaba en silencio. Reconciliando, el <li> es el mismo nodo
// mientras el job exista.
const filas = new Map();

function crearFila(job) {
  const li = document.createElement("li");
  li.dataset.id = job.id;

  const title = document.createElement("div");
  title.className = "job-title";
  const num = document.createElement("span");
  num.className = "job-num";
  const texto = document.createTextNode("");
  title.append(num, texto);

  const bar = document.createElement("div");
  bar.className = "bar";
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", "100");
  const fill = document.createElement("span");
  bar.appendChild(fill);

  const meta = document.createElement("div");
  meta.className = "job-meta";
  const left = document.createElement("span");
  const right = document.createElement("span");
  meta.append(left, right);

  const err = document.createElement("div");
  err.className = "job-error hidden";

  const acciones = document.createElement("div");
  acciones.className = "job-actions";
  const cancelar = document.createElement("button");
  cancelar.className = "link";
  cancelar.textContent = "Cancelar";
  cancelar.addEventListener("click", async () => {
    try {
      await api.cancel(li.dataset.id);
      await refreshJobs();
    } catch (e) {
      showMessage(e.message, "error");
    }
  });
  const reintentar = document.createElement("button");
  reintentar.className = "link";
  reintentar.textContent = "Reintentar";
  reintentar.title = "Vuelve a encolarla con las opciones que estén elegidas ahora";
  reintentar.addEventListener("click", async () => {
    const u = li.dataset.url;
    // Las cookies se piden por la URL del CURSO, no por la de la clase: la de la
    // clase la generó yt-dlp sobre `www.udemy.com`, y en un portal de Udemy
    // Business ahí no viven las cookies host-only que autentican.
    const paraCookies = li.dataset.cookieUrl || u;
    try {
      await api.download({ url: u, cookies: await cookiesIfWanted(paraCookies), ...opcionesActuales() });
      showMessage("Reencolada.", "info");
      await refreshJobs();
    } catch (e) {
      showMessage(e.message, "error");
    }
  });
  const copiar = document.createElement("button");
  copiar.className = "link";
  copiar.textContent = "Copiar URL";
  copiar.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(li.dataset.url || "");
      showMessage("URL copiada.", "info");
    } catch {
      showMessage("El navegador no dejó copiar al portapapeles.", "error");
    }
  });
  acciones.append(cancelar, reintentar, copiar);

  li.append(title, bar, meta, err, acciones);
  li._partes = { num, texto, bar, fill, left, right, err, cancelar, reintentar, copiar, acciones };
  return li;
}

// Sólo se toca lo que cambió: asignar el mismo textContent no dispara relayout,
// pero además así el diff queda explícito y no hay nodos nuevos por tick.
function poner(nodo, prop, valor) {
  if (nodo[prop] !== valor) nodo[prop] = valor;
}

function pintarFila(li, job) {
  const p = li._partes;
  li.dataset.url = job.url || "";
  li.dataset.cookieUrl = job.cookie_url || "";

  if (job.position) {
    poner(p.num, "textContent", `${job.position}/${job.total}`);
    poner(p.num, "className", "job-num");
  } else {
    poner(p.num, "textContent", "");
    poner(p.num, "className", "job-num hidden");
  }
  poner(p.texto, "nodeValue", job.title || job.url || "");
  li.title = job.url || "";

  const fallo = job.status === "error" || job.status === "upload_error";
  const clase =
    job.status === "done" ? "done" : job.status === "skipped" ? "skipped" : fallo ? "error" : "";
  const pct = job.status === "done" ? 100 : Number(job.percent) || 0;
  poner(p.bar, "className", `bar ${clase}`.trim());
  p.bar.setAttribute("aria-valuenow", String(Math.round(pct)));
  p.bar.setAttribute("aria-label", `${LABELS[job.status] || job.status}: ${Math.round(pct)}%`);
  if (p.fill.style.width !== `${pct}%`) p.fill.style.width = `${pct}%`;

  poner(p.left, "textContent", LABELS[job.status] || job.status);
  let derecha = "";
  if (job.status === "running") {
    derecha = [job.speed, job.eta != null ? `${job.eta}s` : null].filter(Boolean).join(" · ");
  } else if (job.status === "waiting" && job.wait_seconds != null) {
    derecha = `arranca en ${job.wait_seconds}s`;
  } else if (job.status === "uploading") {
    derecha = `${job.percent}%`;
  }
  poner(p.right, "textContent", derecha);

  poner(p.err, "textContent", job.error || "");
  // El crudo de yt-dlp va al tooltip: ocupa mucho para mostrarlo siempre, pero
  // cuando la traducción se equivoca es lo único que sirve.
  p.err.title = job.error_detalle || "";
  poner(p.err, "className", job.error ? "job-error" : "job-error hidden");

  const activo = ACTIVOS.includes(job.status);
  poner(p.cancelar, "className", activo ? "link" : "link hidden");
  const reintentable = REINTENTABLES.includes(job.status);
  poner(p.reintentar, "className", reintentable ? "link" : "link hidden");
  poner(p.copiar, "className", reintentable ? "link" : "link hidden");
  poner(p.acciones, "className", activo || reintentable ? "job-actions" : "job-actions hidden");
}

function pintarVacio(list, texto) {
  let li = list.querySelector("li.vacio");
  if (!li) {
    li = document.createElement("li");
    li.className = "vacio job-meta";
    list.appendChild(li);
  }
  poner(li, "textContent", texto);
}

async function refreshJobs() {
  let jobs, sesion;
  try {
    ({ jobs, sesion } = await api.jobs());
    marcarEstado(true, "Servidor conectado");
  } catch (e) {
    // Un token mal configurado NO es un servidor caído: /health no pide token,
    // así que el punto quedaba verde y la lista vacía, sin ninguna pista.
    if (e instanceof ErrorDeToken) {
      marcarEstado(false, "Token inválido");
      showMessage(e.message, "error");
    } else {
      marcarEstado(false, "Servidor no disponible");
    }
    return;
  }

  pintarSesion(sesion);

  const list = $("job-list");

  if (!jobs.length) {
    for (const [id, li] of filas) {
      li.remove();
      filas.delete(id);
    }
    pintarVacio(list, "Todavía no hay descargas.");
    $("cancel-all").className = "link hidden";
    $("refresh-session").className = "link hidden";
    return;
  }
  list.querySelector("li.vacio")?.remove();

  // En orden de encolado, NO al revés: para un curso ese es el orden de las
  // clases. Mostrarlo invertido dejaba la clase 40 arriba y la 1 abajo.
  const vistos = new Set();
  let anterior = null;
  for (const job of jobs) {
    vistos.add(job.id);
    let li = filas.get(job.id);
    if (!li) {
      li = crearFila(job);
      filas.set(job.id, li);
    }
    // Reubica sólo si hace falta: insertBefore con el nodo ya en posición es
    // un no-op para el navegador, pero mover un nodo enfocado le saca el foco.
    const esperado = anterior ? anterior.nextSibling : list.firstChild;
    if (li !== esperado) list.insertBefore(li, esperado);
    pintarFila(li, job);
    anterior = li;
  }

  for (const [id, li] of filas) {
    if (!vistos.has(id)) {
      li.remove();
      filas.delete(id);
    }
  }

  const activos = jobs.filter((j) => ACTIVOS.includes(j.status)).length;
  $("cancel-all").className = activos ? "link" : "link hidden";
  $("cancel-all").textContent = `Cancelar todo (${activos})`;
}

init();
