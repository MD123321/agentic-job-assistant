"use strict";

function isSubmissionControl(element) {
  const type = String(element.type || "").toLowerCase();
  return ["submit", "button", "reset", "image", "hidden"].includes(type);
}

function collectPageSnapshot() {
  const isSubmission = (element) =>
    ["submit", "button", "reset", "image", "hidden"].includes(
      String(element.type || "").toLowerCase()
    );
  const labelFor = (element) => {
    const explicit = element.id
      ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)
      : null;
    const wrapping = element.closest ? element.closest("label") : null;
    return String(
      explicit?.innerText ||
        wrapping?.innerText ||
        element.getAttribute?.("aria-label") ||
        element.placeholder ||
        element.name ||
        element.id ||
        "Uten feltnavn"
    ).trim().slice(0, 500);
  };
  const describedText = (element) => String(
    element.getAttribute?.("aria-describedby") || ""
  ).split(/\s+/).filter(Boolean).map((id) => document.getElementById?.(id)?.innerText || "")
    .filter(Boolean).join(" ").trim().slice(0, 1000);
  const groupLabelFor = (element) => {
    const fieldset = element.closest?.("fieldset");
    const legend = fieldset?.querySelector?.("legend");
    if (legend?.innerText) return String(legend.innerText).trim().slice(0, 500);
    const group = element.closest?.('[role="group"], [role="radiogroup"]');
    if (!group) return "";
    const labelledBy = String(group.getAttribute?.("aria-labelledby") || "");
    const labelledText = labelledBy.split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById?.(id)?.innerText || "").filter(Boolean).join(" ");
    return String(labelledText || group.getAttribute?.("aria-label") || "").trim().slice(0, 500);
  };
  const nearbyTextFor = (element) => {
    const container = element.closest?.(
      "fieldset, [role=group], [role=radiogroup], .form-group, .field, .question"
    ) || element.parentElement;
    return String(container?.innerText || "").trim().slice(0, 1500);
  };
  const controlsForApplication = () => {
    const forms = Array.from(document.querySelectorAll("form")).filter(
      (candidate) => String(candidate.tagName || "").toLowerCase() === "form" &&
        typeof candidate.querySelectorAll === "function"
    );
    const ranked = forms.map((form) => ({
      form,
      controls: Array.from(form.querySelectorAll("input, textarea, select")),
    })).sort((left, right) =>
      right.controls.filter((item) => !isSubmission(item) && !item.disabled).length -
      left.controls.filter((item) => !isSubmission(item) && !item.disabled).length
    );
    return ranked[0]?.controls || Array.from(
      document.querySelectorAll("input, textarea, select")
    );
  };
  const controls = controlsForApplication();
  const seenRadioGroups = new Set();
  const fields = [];

  controls.forEach((element, domIndex) => {
    const tag = element.tagName.toLowerCase();
    let fieldType = tag === "textarea" || tag === "select" ? tag : String(element.type || "text").toLowerCase();
    if (isSubmission(element)) return;
    if (fieldType === "radio" && element.name) {
      if (seenRadioGroups.has(element.name)) return;
      seenRadioGroups.add(element.name);
    }
    const ownLabel = labelFor(element);
    const groupLabel = groupLabelFor(element);
    if (
      tag === "input" &&
      ((fieldType === "search" && !element.name) ||
        (!element.name && !element.id && !element.autocomplete && ownLabel === "Uten feltnavn"))
    ) return;
    let options = [];
    if (fieldType === "select") {
      options = Array.from(element.options).map((option) => option.text || option.value).filter(Boolean);
    } else if (fieldType === "radio" && element.name) {
      options = controls
        .filter((candidate) => candidate.type === "radio" && candidate.name === element.name)
        .map((candidate) => labelFor(candidate) || candidate.value)
        .filter(Boolean);
    } else if (fieldType === "checkbox") {
      options = [ownLabel || String(element.value || "Valgt")];
    }
    fields.push({
      field_id: `field-${domIndex}`,
      dom_index: domIndex,
      label: fieldType === "radio" ? (groupLabel || ownLabel) : ownLabel,
      name: String(element.name || "").slice(0, 200),
      element_id: String(element.id || "").slice(0, 200),
      tag,
      field_type: fieldType,
      autocomplete: String(element.autocomplete || "").slice(0, 100),
      placeholder: String(element.placeholder || "").slice(0, 500),
      required: Boolean(element.required),
      disabled: Boolean(element.disabled),
      options,
      group_label: groupLabel,
      help_text: describedText(element),
      nearby_text: nearbyTextFor(element),
      input_value: ["radio", "checkbox"].includes(fieldType)
        ? String(element.value || "").slice(0, 300)
        : "",
      accept: String(element.accept || "").slice(0, 300),
    });
  });

  return {
    fields,
    page: {
      title: document.title.slice(0, 500),
      url: location.href.slice(0, 2000),
      visible_text: String(document.body?.innerText || "").slice(0, 6000),
    },
  };
}

function fillAssignments(assignments) {
  const isSubmission = (element) =>
    ["submit", "button", "reset", "image", "hidden"].includes(
      String(element.type || "").toLowerCase()
    );
  const labelFor = (element) => {
    const explicit = element.id
      ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)
      : null;
    const wrapping = element.closest ? element.closest("label") : null;
    return String(
      explicit?.innerText ||
        wrapping?.innerText ||
        element.getAttribute?.("aria-label") ||
        element.value ||
        ""
    ).trim();
  };
  const controlsForApplication = () => {
    const forms = Array.from(document.querySelectorAll("form")).filter(
      (candidate) => String(candidate.tagName || "").toLowerCase() === "form" &&
        typeof candidate.querySelectorAll === "function"
    );
    const ranked = forms.map((form) => ({
      form,
      controls: Array.from(form.querySelectorAll("input, textarea, select")),
    })).sort((left, right) =>
      right.controls.filter((item) => !isSubmission(item) && !item.disabled).length -
      left.controls.filter((item) => !isSubmission(item) && !item.disabled).length
    );
    return ranked[0]?.controls || Array.from(
      document.querySelectorAll("input, textarea, select")
    );
  };
  const controls = controlsForApplication();
  const outcome = { filled: [], skipped: [], failed: [] };

  for (const assignment of assignments) {
    const element = controls[assignment.dom_index];
    if (
      !element ||
      isSubmission(element) ||
      element.disabled ||
      (assignment.name && String(element.name || "") !== String(assignment.name)) ||
      (assignment.element_id && String(element.id || "") !== String(assignment.element_id))
    ) {
      outcome.skipped.push({ field_id: assignment.field_id, reason: "Kontrollfelt eller utilgjengelig felt." });
      continue;
    }
    const type = String(element.type || element.tagName || "").toLowerCase();
    if (type === "file") {
      if (!assignment.allow_file || !assignment.document?.base64) {
        outcome.skipped.push({ field_id: assignment.field_id, reason: "Dokumentet er ikke godkjent." });
        continue;
      }
      try {
        const binary = atob(assignment.document.base64);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
          bytes[index] = binary.charCodeAt(index);
        }
        const file = new File(
          [bytes],
          assignment.document.filename,
          { type: assignment.document.mime_type || "application/octet-stream" }
        );
        const transfer = new DataTransfer();
        transfer.items.add(file);
        element.files = transfer.files;
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        if (element.files?.length !== 1 || element.files[0].name !== file.name) {
          throw new Error("Filfeltet beholdt ikke dokumentet.");
        }
        outcome.filled.push(assignment.field_id);
      } catch (error) {
        outcome.failed.push({ field_id: assignment.field_id, reason: String(error.message || error) });
      }
      continue;
    }
    try {
      if (type === "checkbox") {
        if (!assignment.allow_checkbox) {
          outcome.skipped.push({ field_id: assignment.field_id, reason: "Avkrysning er ikke godkjent." });
          continue;
        }
        const wanted = String(assignment.value).trim().toLowerCase();
        const label = labelFor(element).toLowerCase();
        const allowed = [label, String(element.value || "").toLowerCase()];
        if (!wanted || !allowed.includes(wanted)) throw new Error("Avkrysningsverdien stemmer ikke.");
        element.checked = true;
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        if (!element.checked) throw new Error("Avkrysningsfeltet beholdt ikke verdien.");
      } else if (type === "radio") {
        const radios = controls.filter(
          (candidate) => candidate.type === "radio" && candidate.name === assignment.name
        );
        const wanted = String(assignment.value).trim().toLowerCase();
        const selected = radios.find((candidate) => {
          const label = labelFor(candidate).toLowerCase();
          return String(candidate.value).toLowerCase() === wanted || label === wanted;
        });
        if (!selected) throw new Error("Fant ikke radioalternativet.");
        selected.checked = true;
        selected.dispatchEvent(new Event("change", { bubbles: true }));
        if (!selected.checked) throw new Error("Radiofeltet beholdt ikke den foreslåtte verdien.");
      } else if (element.tagName.toLowerCase() === "select") {
        const wanted = String(assignment.value).trim().toLowerCase();
        const option = Array.from(element.options).find(
          (candidate) =>
            String(candidate.value).toLowerCase() === wanted ||
            String(candidate.text).trim().toLowerCase() === wanted
        );
        if (!option) throw new Error("Fant ikke nedtrekksalternativet.");
        element.value = option.value;
        element.dispatchEvent(new Event("change", { bubbles: true }));
        if (element.value !== option.value) throw new Error("Nedtrekksfeltet beholdt ikke verdien.");
      } else {
        element.focus();
        element.value = String(assignment.value);
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        if (element.value !== String(assignment.value)) {
          throw new Error("Feltet beholdt ikke den foreslåtte verdien.");
        }
      }
      outcome.filled.push(assignment.field_id);
    } catch (error) {
      outcome.failed.push({ field_id: assignment.field_id, reason: String(error.message || error) });
    }
  }
  return outcome;
}

if (typeof module !== "undefined") {
  module.exports = { collectPageSnapshot, isSubmissionControl, fillAssignments };
}
