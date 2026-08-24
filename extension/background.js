// Watches running jobs and notifies when one finishes, so you can close the
// popup and still find out. The popup does its own polling while open.
//
// El estado NO puede vivir en memoria. Este es un service worker MV3 y Chrome lo
// mata a los ~30 s sin eventos: el `Map` que guardaba el estado anterior de cada
// job arrancaba vacío en cada alarma, la guarda de "primer avistamiento" cortaba
// antes de notificar, y el módulo no cumplía NUNCA lo que promete arriba.
// `chrome.storage.session` sobrevive al reciclado del worker y se limpia sola al
// cerrar el navegador, que es exactamente la duración que queremos.

import { api, collectCookies } from "./api.js";

const POLL_ALARM = "video-dl-poll";
const CLAVE_NOTIFICADOS = "jobsNotificados";
const CLAVE_ULTIMO_REFRESCO = "ultimoRefrescoSesion";
const CLAVE_SEMBRADO = "notificadorSembrado";
// Un curso son 200 jobs; el tope evita que la lista crezca sin control en una
// sesión larga de navegador.
const MAX_RECORDADOS = 600;

// Chrome no respeta períodos menores a 30 s (0.5 min): pedir 0.15 no daba un
// sondeo cada 9 s, daba el mínimo de la plataforma igual. Se pide el mínimo
// real para no depender de un clamp que no está documentado como estable.
const PERIODO_MINUTOS = 0.5;

const ACTIVOS = new Set(["queued", "waiting", "running", "postprocessing", "uploading"]);
const TERMINALES = new Set(["done", "skipped", "error", "upload_error", "cancelled"]);

function crearAlarma() {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: PERIODO_MINUTOS });
}

chrome.runtime.onInstalled.addListener(crearAlarma);
chrome.runtime.onStartup.addListener(crearAlarma);
// También al arrancar el worker: si la alarma se perdió por cualquier motivo,
// esto la repone sin esperar a una reinstalación.
crearAlarma();

async function leerEstado() {
  const datos = await chrome.storage.session.get({
    [CLAVE_NOTIFICADOS]: [],
    [CLAVE_SEMBRADO]: false,
  });
  return {
    notificados: new Set(datos[CLAVE_NOTIFICADOS]),
    sembrado: datos[CLAVE_SEMBRADO],
  };
}

async function guardarEstado(notificados) {
  await chrome.storage.session.set({
    [CLAVE_NOTIFICADOS]: [...notificados].slice(-MAX_RECORDADOS),
    [CLAVE_SEMBRADO]: true,
  });
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== POLL_ALARM) return;
  let jobs, sesion;
  try {
    ({ jobs, sesion } = await api.jobs());
  } catch {
    return; // server down or token unset; nothing to report
  }

  await quizasRefrescarSesion(sesion);

  const { notificados, sembrado } = await leerEstado();

  // La primera vez en esta sesión de navegador puede haber decenas de trabajos
  // ya terminados de antes. Se registran sin avisar: si no, abrir Chrome con un
  // curso viejo en el historial dispararía 200 notificaciones de una.
  if (!sembrado) {
    for (const job of jobs) {
      if (TERMINALES.has(job.status)) notificados.add(job.id);
    }
    await guardarEstado(notificados);
    actualizarBadge(jobs);
    return;
  }

  let hubo = false;
  for (const job of jobs) {
    if (!TERMINALES.has(job.status) || notificados.has(job.id)) continue;
    notificados.add(job.id);
    hubo = true;

    if (job.status === "done") {
      notify(job.id, "Descarga lista", job.title || job.url);
    } else if (job.status === "error") {
      notify(job.id, "Falló la descarga", job.error || job.url);
    } else if (job.status === "upload_error") {
      notify(job.id, "Bajada, pero sin subir", job.error || job.title || job.url);
    }
    // `skipped` y `cancelled` se registran para no re-evaluarlos, pero no
    // molestan con una notificación: no pasó nada que el usuario no supiera.
  }

  if (hubo || !sembrado) await guardarEstado(notificados);
  actualizarBadge(jobs);
});

// Refresca la sesión de la cola desde el navegador, sin que el usuario haga nada.
//
// Las cookies se capturan UNA vez, al encolar. Un curso de 200 clases en preset
// cauteloso son horas, y el token de Udemy rota en el medio: todas las clases
// que faltaban salían con una sesión muerta y el sitio devolvía 403. Este worker
// ya se despierta cada 30 s para el badge, así que refrescar desde acá no cuesta
// ni un pedido de más al sitio: las cookies se leen del propio navegador.
const MARGEN_VENCIMIENTO_MS = 30 * 60_000;   // refrescar si vence dentro de esto
const CADA_MS = 5 * 60_000;                  // y como mucho con esta frecuencia

async function quizasRefrescarSesion(sesion) {
  // `sesion.url` es la URL con la que se juntaron las cookies, y tiene que ser
  // ESA: en un portal de Udemy Business las clases se encolan como
  // `www.udemy.com/...` y ahí no viven las cookies host-only del portal, que son
  // justamente las que autentican.
  if (!sesion?.pendientes || !sesion.url) return;

  const ahora = Date.now();
  const { [CLAVE_ULTIMO_REFRESCO]: ultimo = 0 } = await chrome.storage.session.get({
    [CLAVE_ULTIMO_REFRESCO]: 0,
  });
  if (ahora - ultimo < CADA_MS) return;

  // `vence_en` en null son cookies de sesión, sin fecha: no se puede anticipar
  // el problema, así que se refresca igual cada CADA_MS.
  if (sesion.vence_en != null && sesion.vence_en * 1000 - ahora > MARGEN_VENCIMIENTO_MS) return;

  try {
    const cookies = await collectCookies(sesion.url);
    if (!cookies.length) return;
    await api.refreshCookies(cookies);
    await chrome.storage.session.set({ [CLAVE_ULTIMO_REFRESCO]: ahora });
  } catch {
    // El navegador puede negar las cookies, o el server puede estar cayéndose.
    // No se reintenta acá: la próxima alarma lo vuelve a intentar sola.
  }
}

function actualizarBadge(jobs) {
  // `waiting` y `uploading` faltaban: durante las pausas del throttle —que en
  // preset cauteloso son la mayor parte del tiempo— el badge se vaciaba y
  // parecía que no había nada en curso.
  const activos = jobs.filter((j) => ACTIVOS.has(j.status)).length;
  chrome.action.setBadgeText({ text: activos ? String(activos) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
}

function notify(jobId, title, message) {
  // El id estable evita apilar tarjetas duplicadas del mismo job si la alarma
  // llegara a procesarlo dos veces.
  chrome.notifications.create(`video-dl-${jobId}`, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title,
    message: String(message || "").slice(0, 200),
  });
}
