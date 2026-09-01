const zonesRoot = document.getElementById("zones");

function formatValue(value, suffix = "") {
  return value === null || value === undefined ? "n/a" : `${value}${suffix}`;
}

function stageTimelineMarkup(zone) {
  const plan = zone.stagePlan || [];
  if (!plan.length) {
    return "";
  }
  const rows = plan.map((item) => `
        <li class="stage-row stage-${item.state}">
          <span class="stage-name">${item.name}</span>
          <span class="stage-dates">${item.startDate} &rarr; ${item.endDate}</span>
          <span class="stage-days">d${item.startDay}-${item.endDay}</span>
        </li>`).join("");
  const remaining = zone.daysToHarvest;
  const remainingText = remaining === null || remaining === undefined
    ? ""
    : ` (${remaining > 0 ? remaining + "d left" : Math.abs(remaining) + "d overdue"})`;
  return `
      <details class="stage-plan">
        <summary>Projected stages &middot; harvest ~${zone.harvestDate ?? "n/a"}${remainingText}</summary>
        <ol class="stages">${rows}</ol>
      </details>`;
}

function plantOptions(selected) {
  return Object.entries(window.plantCatalog || {})
    .map(([key, p]) => `<option value="${key}"${key === selected ? " selected" : ""}>${p.label}</option>`)
    .join("");
}

function zoneCardMarkup(zone, isAdmin) {
  const reservoir = zone.zone === "zone001"
    ? `<div>Reservoir: <strong>${formatValue(zone.waterLevel, "%")}</strong></div>`
    : "";

  const controls = isAdmin ? `
    <div class="controls">
      <button class="btn cmd" data-command="valve" data-value="open">Open Valve</button>
      <button class="btn cmd" data-command="valve" data-value="close">Close Valve</button>
      <div class="planting">
        <label>Plant <select class="plant-select">${plantOptions(zone.plantType)}</select></label>
        <label>Planted <input type="date" class="plant-date" value="${zone.plantedAt ?? ""}"></label>
        <button class="btn apply-plant">Apply</button>
      </div>
    </div>
  ` : "";

  return `
    <article class="zone-card ${zone.stale ? "stale" : ""}" data-zone="${zone.zone}">
      <div class="zone-header">
        <h2>${zone.entityId}</h2>
        <span class="badge ${zone.irrigationClass}">${zone.irrigationLabel}</span>
      </div>
      <p class="zone-meta">Observed: ${zone.observedAt ?? "n/a"}</p>
      ${zone.stale ? `<p class="stale-text">${zone.staleText}</p>` : ""}
      <div class="metrics">
        <div>Soil Moisture: <strong>${formatValue(zone.soilMoisture, "%")}</strong></div>
        <div>Air Temp: <strong>${formatValue(zone.airTemperature, "°C")}</strong></div>
        <div>Air Humidity: <strong>${formatValue(zone.airHumidity, "%")}</strong></div>
        <div>Soil EC: <strong>${formatValue(zone.soilConductivity, " µS/cm")}</strong></div>
        <div>Soil pH: <strong>${formatValue(zone.soilPH)}</strong></div>
        <div>Flow Rate: <strong>${formatValue(zone.flowRate, " L/min")}</strong></div>
        ${reservoir}
      </div>
      <div class="band">Moisture band: ${formatValue(zone.moistureMin, "%")} - ${formatValue(zone.moistureMax, "%")}</div>
      <div class="planting-meta">${zone.plantLabel ?? "n/a"} &middot; ${zone.growthStage ?? "n/a"} &middot; day ${zone.daysElapsed ?? 0}</div>
      ${stageTimelineMarkup(zone)}
      <progress max="100" value="${zone.soilMoisture ?? 0}"></progress>
      ${controls}
      <p class="cmd-result" aria-live="polite"></p>
    </article>
  `;
}

function captureEditState() {
  const state = {};
  zonesRoot.querySelectorAll(".zone-card").forEach((card) => {
    const editing = card.dataset.editing === "true";
    const plan = card.querySelector(".stage-plan");
    const planOpen = Boolean(plan && plan.open);
    const result = card.querySelector(".cmd-result")?.textContent;
    const focused = document.activeElement && card.contains(document.activeElement)
      ? document.activeElement.className
      : null;
    if (!editing && !planOpen && !focused && !result) {
      return;
    }
    state[card.dataset.zone] = {
      editing,
      planOpen,
      plant: card.querySelector(".plant-select")?.value,
      date: card.querySelector(".plant-date")?.value,
      result,
      focused,
    };
  });
  return state;
}

function restoreEditState(state) {
  Object.entries(state).forEach(([zoneId, saved]) => {
    const card = zonesRoot.querySelector(`.zone-card[data-zone="${zoneId}"]`);
    if (!card) {
      return;
    }
    const plan = card.querySelector(".stage-plan");
    if (plan && saved.planOpen) {
      plan.open = true;
    }
    if (saved.result) {
      card.querySelector(".cmd-result").textContent = saved.result;
    }
    if (!saved.editing) {
      return;
    }
    card.dataset.editing = "true";
    const select = card.querySelector(".plant-select");
    const dateInput = card.querySelector(".plant-date");
    if (select && saved.plant !== undefined) {
      select.value = saved.plant;
    }
    if (dateInput && saved.date !== undefined) {
      dateInput.value = saved.date;
    }
    if (saved.focused) {
      card.querySelector("." + saved.focused.split(/\s+/)[0])?.focus();
    }
  });
}

// A 30s poll rewrites every card. Without this, a half-finished plant
// selection is silently reset under the operator's cursor.
function renderZones(zones, isAdmin) {
  const editState = captureEditState();
  zonesRoot.innerHTML = zones.map((zone) => zoneCardMarkup(zone, isAdmin)).join("");
  restoreEditState(editState);
}

async function sendCommand(zone, command, value, resultElement) {
  resultElement.textContent = "Sending...";
  const response = await fetch("/api/cmd", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ zone, command, value }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    resultElement.textContent = payload.error || "Command failed";
    return;
  }
  resultElement.textContent = `Ack: ${payload.ack || payload.payload}`;
}

async function sendPlant(zone, plant, plantedAt, resultElement) {
  resultElement.textContent = "Applying...";
  const response = await fetch("/api/plant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ zone, plant, planted_at: plantedAt }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    resultElement.textContent = payload.error || "Plant update failed";
    return false;
  }
  const acked = (payload.acks || []).map((item) => item.command).join(", ");
  resultElement.textContent = `Set to ${payload.plant} (${acked})`;
  return true;
}

async function pollZones(isAdmin) {
  try {
    const response = await fetch("/api/zones");
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    if (!data.ok) {
      return;
    }
    renderZones(data.zones, isAdmin);
  } catch (err) {
    // ignore poll errors and keep last state
  }
}

function bindEvents(isAdmin) {
  zonesRoot.addEventListener("click", async (event) => {
    const card = event.target.closest(".zone-card");
    if (!card) {
      return;
    }
    const resultElement = card.querySelector(".cmd-result");

    if (event.target.classList.contains("cmd")) {
      await sendCommand(
        card.dataset.zone,
        event.target.dataset.command,
        event.target.dataset.value,
        resultElement,
      );
    }

    if (event.target.classList.contains("apply-plant")) {
      const plant = card.querySelector(".plant-select").value;
      const plantedAt = card.querySelector(".plant-date").value;
      const ok = await sendPlant(card.dataset.zone, plant, plantedAt, resultElement);
      if (ok) {
        card.dataset.editing = "false";
      }
    }
  });

  ["change", "input"].forEach((evt) => {
    zonesRoot.addEventListener(evt, (event) => {
      if (event.target.classList.contains("plant-select")
          || event.target.classList.contains("plant-date")) {
        const card = event.target.closest(".zone-card");
        if (card) {
          card.dataset.editing = "true";
        }
      }
    });
  });

  if (isAdmin) {
    setInterval(() => pollZones(isAdmin), 30000);
  } else {
    setInterval(() => pollZones(false), 30000);
  }
}

const isAdmin = zonesRoot?.dataset.isAdmin === "true";
if (zonesRoot) {
  bindEvents(isAdmin);
  pollZones(isAdmin);
}
