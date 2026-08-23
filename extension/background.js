// Watches running jobs and notifies when one finishes, so you can close the
// popup and still find out. The popup does its own polling while open.
//
// El estado NO puede vivir en memoria. Este es un service worker MV3 y Chrome lo
// mata a los ~30 s sin eventos: el `Map` que guardaba el estado anterior de cada
// job arrancaba vacío en cada alarma, la guarda de "primer avistamiento" cortaba
// antes de notificar, y el módulo no cumplía NUNCA lo que promete arriba.
// `chrome.storage.session` sobrevive al reciclado del worker y se limpia sola al
// cerrar el navegador, que es exactamente la duración que queremos.

import { api } from "./api.js";

const POLL_ALARM = "video-dl-poll";
const CLAVE_NOTIFICADOS = "jobsNotificados";
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
  let jobs;
  try {
    ({ jobs } = await api.jobs());
  } catch {
    return; // server down or token unset; nothing to report
  }

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
