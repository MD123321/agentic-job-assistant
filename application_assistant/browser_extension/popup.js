"use strict";

const SERVICE_URL = "http://127.0.0.1:8765";
const state = { tabId: null, plan: null };

const tokenInput = document.getElementById("token");
const scanButton = document.getElementById("scan");
const fillButton = document.getElementById("fill");
const statusNode = document.getElementById("status");
const previewNode = document.getElementById("preview");
const summaryNode = document.getElementById("summary");
const cvNode = document.getElementById("cv");
const groupsNode = document.getElementById("field-groups");
const resultNode = document.getElementById("result");

function setStatus(message, isError = false) {
  statusNode.textContent = message;
  statusNode.style.color = isError ? "#a42828" : "#385347";
}

async function storedToken() {
  const value = await chrome.storage.local.get("jobbagentServiceToken");
  return value.jobbagentServiceToken || "";
}

async function saveToken() {
  const token = tokenInput.value.trim();
  await chrome.storage.local.set({ jobbagentServiceToken: token });
  setStatus(token ? "Lokalt token er lagret i Chrome." : "Token er fjernet.");
}

async function requestPlan(snapshot) {
  const token = tokenInput.value.trim();
  if (!token) throw new Error("Lim inn det lokale tjenestetokenet først.");
  const response = await fetch(`${SERVICE_URL}/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Jobbagent-Token": token },
    body: JSON.stringify(snapshot),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Den lokale tjenesten svarte med en feil.");
  return payload;
}

async function requestDocument(documentKey) {
  const token = tokenInput.value.trim();
  const response = await fetch(`${SERVICE_URL}/document`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Jobbagent-Token": token },
    body: JSON.stringify({ document_key: documentKey }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Kunne ikke hente lokalt dokument.");
  return payload;
}

function fieldEditor(field) {
  const card = document.createElement("div");
  card.className = `field-card category-${field.category}`;
  card.dataset.fieldId = field.field_id;

  const canEdit = Boolean(field.fillable);
  const hasControl = canEdit && ["auto", "review", "requires_action"].includes(
    field.category
  );
  const title = document.createElement("label");
  if (hasControl) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "include-field";
    checkbox.checked = Boolean(field.selected_by_default);
    title.appendChild(checkbox);
  }
  const titleText = document.createElement("span");
  titleText.textContent = field.label;
  title.appendChild(titleText);
  card.appendChild(title);

  if (hasControl && field.field_type !== "file") {
    let editor;
    if (["select", "radio", "checkbox"].includes(field.field_type) && field.options?.length) {
      editor = document.createElement("select");
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Velg svar";
      editor.appendChild(placeholder);
      for (const optionValue of field.options) {
        const option = document.createElement("option");
        option.value = optionValue;
        option.textContent = optionValue;
        editor.appendChild(option);
      }
    } else if (field.category !== "auto" || field.field_type === "textarea") {
      editor = document.createElement("textarea");
    } else {
      editor = document.createElement("input");
    }
    editor.className = "field-value";
    editor.value = field.value;
    editor.readOnly = field.category === "auto" && editor.tagName !== "SELECT";
    editor.addEventListener("input", () => {
      const include = card.querySelector(".include-field");
      if (include && !editor.value) include.checked = false;
    });
    card.appendChild(editor);
  } else if (hasControl && field.field_type === "file") {
    const filename = document.createElement("p");
    filename.className = "field-value file-value";
    filename.textContent = field.value;
    card.appendChild(filename);
  }
  const reason = document.createElement("p");
  reason.className = "reason";
  reason.textContent = field.reason;
  card.appendChild(reason);
  if (field.meaning || field.confidence || field.sensitive) {
    const metadata = document.createElement("p");
    metadata.className = "evidence";
    const parts = [];
    if (field.meaning) parts.push(`Tolket som: ${field.meaning}`);
    if (field.confidence) parts.push(`Sikkerhet: ${field.confidence}`);
    if (field.sensitive) parts.push("Sensitivt felt");
    metadata.textContent = parts.join(" · ");
    card.appendChild(metadata);
  }
  if (field.evidence_keys?.length) {
    const evidence = document.createElement("p");
    evidence.className = "evidence";
    evidence.textContent = `Grunnlag: ${field.evidence_keys.join(", ")}`;
    card.appendChild(evidence);
  }
  return card;
}

function renderPlan(plan) {
  state.plan = plan;
  summaryNode.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "summary-grid";
  const labels = [
    ["Fylles automatisk", plan.summary.auto],
    ["Forslag til kontroll", plan.summary.review],
    ["Mangler svar", plan.summary.requires_action],
    ["Ikke støttet", plan.summary.unsupported],
  ];
  for (const [label, count] of labels) {
    const item = document.createElement("div");
    item.textContent = `${label}: ${count}`;
    grid.appendChild(item);
  }
  summaryNode.appendChild(grid);
  const ai = document.createElement("p");
  ai.className = "reason";
  ai.textContent = `AI: ${plan.ai.message}`;
  summaryNode.appendChild(ai);

  cvNode.textContent = plan.cv.filename
    ? `CV: ${plan.cv.filename} – klar for opplasting når du fyller inn valgte felt.`
    : "CV: Ingen standard-CV er konfigurert.";
  groupsNode.innerHTML = "";
  const categories = [
    ["auto", "Fylles automatisk"],
    ["review", "Forslag som må kontrolleres"],
    ["requires_action", "Krever handling fra deg"],
    ["unsupported", "Ikke støttet"],
  ];
  for (const [category, title] of categories) {
    const fields = plan.fields.filter((field) => field.category === category);
    if (!fields.length) continue;
    const heading = document.createElement("h2");
    heading.className = "group-title";
    heading.textContent = title;
    groupsNode.appendChild(heading);
    fields.forEach((field) => groupsNode.appendChild(fieldEditor(field)));
  }
  previewNode.hidden = false;
  resultNode.hidden = true;
}

async function scanAndPreview() {
  scanButton.disabled = true;
  setStatus("Leser synlige felt og lager forhåndsvisning …");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("Fant ingen aktiv nettleserfane.");
    state.tabId = tab.id;
    const [execution] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectPageSnapshot,
    });
    const plan = await requestPlan(execution.result);
    renderPlan(plan);
    setStatus("Forhåndsvisningen er klar. Kontroller alt før du fyller inn.");
  } catch (error) {
    setStatus(String(error.message || error), true);
  } finally {
    scanButton.disabled = false;
  }
}

async function selectedAssignments() {
  if (!state.plan) return [];
  const assignments = state.plan.fields.flatMap((field) => {
    const card = groupsNode.querySelector(`[data-field-id="${CSS.escape(field.field_id)}"]`);
    const include = card?.querySelector(".include-field");
    const editor = card?.querySelector(".field-value");
    if (!include?.checked || (!editor && field.field_type !== "file")) return [];
    return [{
      field_id: field.field_id,
      dom_index: field.dom_index,
      name: field.name,
      element_id: field.element_id,
      value: field.field_type === "file" ? field.value : editor.value,
      field_type: field.field_type,
      allow_checkbox: field.field_type === "checkbox" && Boolean(field.fillable),
      allow_file: field.field_type === "file" && Boolean(field.document_key),
      document_key: field.document_key,
    }];
  });
  for (const assignment of assignments) {
    if (assignment.allow_file) {
      assignment.document = await requestDocument(assignment.document_key);
    }
  }
  return assignments;
}

async function fillSelected() {
  fillButton.disabled = true;
  try {
    const assignments = await selectedAssignments();
    if (!assignments.length) throw new Error("Velg minst ett felt som skal fylles inn.");
    const [execution] = await chrome.scripting.executeScript({
      target: { tabId: state.tabId },
      func: fillAssignments,
      args: [assignments],
    });
    const outcome = execution.result;
    resultNode.hidden = false;
    resultNode.textContent =
      `Fylt: ${outcome.filled.length}. Hoppet over: ${outcome.skipped.length}. ` +
      `Feil: ${outcome.failed.length}. Kontroller skjemaet og send manuelt.`;
    setStatus("Utfyllingen er ferdig. Jobbagent har ikke sendt søknaden.");
  } catch (error) {
    setStatus(String(error.message || error), true);
  } finally {
    fillButton.disabled = false;
  }
}

document.getElementById("save-token").addEventListener("click", saveToken);
scanButton.addEventListener("click", scanAndPreview);
fillButton.addEventListener("click", fillSelected);
storedToken().then((token) => { tokenInput.value = token; });
