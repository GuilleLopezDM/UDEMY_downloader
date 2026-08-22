// Watches running jobs and notifies when one finishes, so you can close the
// popup and still find out. The popup does its own polling while open.

import { api } from "./api.js";

const POLL_ALARM = "video-dl-poll";
const seen = new Map(); // jobId -> last status we notified about

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.15 });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.15 });
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== POLL_ALARM) return;
  let jobs;
  try {
    ({ jobs } = await api.jobs());
  } catch {
    return; // server down or token unset; nothing to report
  }

  let active = 0;
  for (const job of jobs) {
    if (job.status === "running" || job.status === "queued" || job.status === "postprocessing") {
      active += 1;
    }
    const previous = seen.get(job.id);
    if (previous === job.status) continue;
    seen.set(job.id, job.status);
    if (previous === undefined) continue; // first sighting, don't notify

    if (job.status === "done") {
      notify("Descarga lista", job.title || job.url);
    } else if (job.status === "error") {
      notify("Falló la descarga", job.error || job.url);
    }
  }

  chrome.action.setBadgeText({ text: active ? String(active) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
});

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon128.png",
    title,
    message: message.slice(0, 200),
  });
}
