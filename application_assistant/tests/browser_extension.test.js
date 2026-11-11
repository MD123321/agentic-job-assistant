"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  collectPageSnapshot,
  fillAssignments,
  isSubmissionControl,
} = require("../browser_extension/form_tools.js");

test("submission controls are always identified", () => {
  for (const type of ["submit", "button", "reset", "image", "hidden"]) {
    assert.equal(isSubmissionControl({ type }), true);
  }
});

test("page collection is self-contained for Chrome script injection", () => {
  const input = {
    id: "email",
    name: "email",
    type: "email",
    tagName: "INPUT",
    autocomplete: "email",
    placeholder: "",
    required: true,
    disabled: false,
    closest: () => null,
    getAttribute: () => "Email",
  };
  global.CSS = { escape: (value) => value };
  global.location = { href: "http://127.0.0.1:8000" };
  global.document = {
    title: "Test portal",
    body: { innerText: "Visible job text" },
    querySelector: () => null,
    querySelectorAll: () => [input],
  };

  const snapshot = collectPageSnapshot();

  assert.equal(snapshot.fields.length, 1);
  assert.equal(snapshot.fields[0].label, "Email");
  assert.equal(snapshot.page.url, "http://127.0.0.1:8000");
});

test("page collection uses the largest application form and ignores unrelated controls", () => {
  const makeInput = (id, label) => ({
    id,
    name: id,
    type: "text",
    tagName: "INPUT",
    autocomplete: "",
    placeholder: "",
    required: false,
    disabled: false,
    closest: () => null,
    getAttribute: (name) => name === "aria-label" ? label : "",
  });
  const analytics = makeInput("analytics-cookie", "Analytics");
  const firstName = makeInput("first-name", "First name");
  const lastName = makeInput("last-name", "Last name");
  const cookieForm = {
    tagName: "FORM",
    querySelectorAll: () => [analytics],
  };
  const applicationForm = {
    tagName: "FORM",
    querySelectorAll: () => [firstName, lastName],
  };
  global.CSS = { escape: (value) => value };
  global.location = { href: "https://jobs.example/apply" };
  global.document = {
    title: "Application",
    body: { innerText: "Application fields" },
    querySelector: () => null,
    querySelectorAll: (selector) => selector === "form"
      ? [cookieForm, applicationForm]
      : [analytics, firstName, lastName],
    getElementById: () => null,
  };

  const snapshot = collectPageSnapshot();

  assert.deepEqual(snapshot.fields.map((item) => item.label), ["First name", "Last name"]);
});

test("page collection ignores unnamed internal search inputs", () => {
  const internalSearch = {
    id: "",
    name: "",
    type: "search",
    tagName: "INPUT",
    autocomplete: "",
    placeholder: "",
    required: false,
    disabled: false,
    closest: () => null,
    getAttribute: () => "",
  };
  global.CSS = { escape: (value) => value };
  global.location = { href: "https://jobs.example/apply" };
  global.document = {
    title: "Application",
    body: { innerText: "Application fields" },
    querySelector: () => null,
    querySelectorAll: () => [internalSearch],
    getElementById: () => null,
  };

  assert.deepEqual(collectPageSnapshot().fields, []);
});

test("radio choices inherit their question from the fieldset legend", () => {
  const fieldset = {
    innerText: "Preferred work area Analytics Finance",
    querySelector: () => ({ innerText: "Preferred work area" }),
  };
  const makeRadio = (value) => ({
    id: "",
    name: "work_area",
    type: "radio",
    tagName: "INPUT",
    value,
    autocomplete: "",
    placeholder: "",
    required: false,
    disabled: false,
    closest: (selector) => selector === "fieldset"
      ? fieldset
      : selector === "label" ? { innerText: value } : null,
    getAttribute: () => "",
  });
  const controls = [makeRadio("Analytics"), makeRadio("Finance")];
  global.CSS = { escape: (value) => value };
  global.location = { href: "https://jobs.example/apply" };
  global.document = {
    title: "Application",
    body: { innerText: "Application fields" },
    querySelector: () => null,
    querySelectorAll: () => controls,
    getElementById: () => null,
  };

  const snapshot = collectPageSnapshot();

  assert.equal(snapshot.fields.length, 1);
  assert.equal(snapshot.fields[0].label, "Preferred work area");
  assert.equal(snapshot.fields[0].group_label, "Preferred work area");
  assert.deepEqual(snapshot.fields[0].options, ["Analytics", "Finance"]);
});

test("fillAssignments skips submit, file, and checkbox controls", () => {
  const controls = [
    { type: "submit", disabled: false },
    { type: "file", disabled: false },
    { type: "checkbox", disabled: false },
  ];
  global.document = { querySelectorAll: () => controls };

  const result = fillAssignments([
    { field_id: "submit", dom_index: 0, value: "Send" },
    { field_id: "file", dom_index: 1, value: "/private/cv.pdf" },
    { field_id: "consent", dom_index: 2, value: "yes" },
  ]);

  assert.deepEqual(result.filled, []);
  assert.equal(result.skipped.length, 3);
});

test("plain text fields are filled only when assignments are explicitly provided", () => {
  const events = [];
  const textField = {
    type: "text",
    tagName: "INPUT",
    disabled: false,
    value: "",
    focus() {},
    dispatchEvent(event) { events.push(event.type); },
  };
  global.Event = class Event { constructor(type) { this.type = type; } };
  global.document = { querySelectorAll: () => [textField] };

  assert.deepEqual(fillAssignments([]).filled, []);
  assert.equal(textField.value, "");

  const result = fillAssignments([
    { field_id: "first-name", dom_index: 0, value: "Test" },
  ]);
  assert.deepEqual(result.filled, ["first-name"]);
  assert.equal(textField.value, "Test");
  assert.deepEqual(events, ["input", "change"]);
});

test("simple select and radio choices use explicit documented values", () => {
  const select = {
    type: "select-one",
    tagName: "SELECT",
    disabled: false,
    value: "",
    options: [{ value: "Oslo", text: "Oslo" }, { value: "Bergen", text: "Bergen" }],
    dispatchEvent() {},
  };
  const english = {
    type: "radio",
    tagName: "INPUT",
    name: "language",
    value: "English",
    checked: false,
    disabled: false,
    dispatchEvent() {},
  };
  const norwegian = { ...english, value: "Norwegian" };
  global.document = { querySelectorAll: () => [select, english, norwegian] };

  const result = fillAssignments([
    { field_id: "location", dom_index: 0, value: "Oslo" },
    { field_id: "language", dom_index: 1, name: "language", value: "English" },
  ]);

  assert.deepEqual(result.filled, ["location", "language"]);
  assert.equal(select.value, "Oslo");
  assert.equal(english.checked, true);
});

test("non-sensitive checkbox is filled only with an explicit allowance", () => {
  const events = [];
  const checkbox = {
    id: "",
    type: "checkbox",
    tagName: "INPUT",
    name: "work_area",
    value: "Analytics",
    checked: false,
    disabled: false,
    closest: (selector) => selector === "label" ? { innerText: "Analytics" } : null,
    getAttribute: () => "",
    dispatchEvent(event) { events.push(event.type); },
  };
  global.Event = class Event { constructor(type) { this.type = type; } };
  global.document = { querySelector: () => null, querySelectorAll: () => [checkbox] };

  const result = fillAssignments([
    {
      field_id: "work-area",
      dom_index: 0,
      name: "work_area",
      value: "Analytics",
      allow_checkbox: true,
    },
  ]);

  assert.deepEqual(result.filled, ["work-area"]);
  assert.equal(checkbox.checked, true);
  assert.deepEqual(events, ["input", "change"]);
});

test("configured document is attached only with an explicit allowance", () => {
  const events = [];
  const fileInput = {
    id: "cv",
    name: "cv",
    type: "file",
    tagName: "INPUT",
    files: [],
    disabled: false,
    dispatchEvent(event) { events.push(event.type); },
  };
  global.document = { querySelectorAll: () => [fileInput] };
  global.Event = class Event { constructor(type) { this.type = type; } };
  global.atob = (value) => Buffer.from(value, "base64").toString("binary");
  global.File = class File {
    constructor(_parts, name, options) { this.name = name; this.type = options.type; }
  };
  global.DataTransfer = class DataTransfer {
    constructor() {
      this.files = [];
      this.items = { add: (file) => this.files.push(file) };
    }
  };

  const result = fillAssignments([
    {
      field_id: "cv",
      dom_index: 0,
      name: "cv",
      element_id: "cv",
      allow_file: true,
      document: {
        filename: "cv.pdf",
        mime_type: "application/pdf",
        base64: Buffer.from("pdf").toString("base64"),
      },
    },
  ]);

  assert.deepEqual(result.filled, ["cv"]);
  assert.equal(fileInput.files[0].name, "cv.pdf");
  assert.deepEqual(events, ["input", "change"]);
});
