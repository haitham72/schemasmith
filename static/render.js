/* schemasmith: generic field renderers. AC.fields[type] = {cell, form, parse}. */
(() => {
  const AC = (window.AC = window.AC || {});
  const esc = (s) => AC.esc(s);

  // Reused chips for list-of-domain style values; every value passes through esc().
  AC.domainChips = (domains, max = 2) => {
    if (!domains || !domains.length) return `<span class="dim">Set by admin</span>`;
    const shown = domains.slice(0, max).map((d) => `<span class="chip mono">${esc(d)}</span>`).join("");
    const more = domains.length > max ? `<span class="chip more" title="${esc(domains.slice(max).join(", "))}">+${domains.length - max}</span>` : "";
    return `<div class="chips">${shown}${more}</div>`;
  };

  const text = (v) => (v === null || v === undefined || v === "" ? `<span class="dim">—</span>` : esc(v));
  const mono = (v) => (v === null || v === undefined || v === "" ? `<span class="dim">—</span>` : `<span class="mono">${esc(v)}</span>`);

  const listCell = (values, renderOne) => {
    if (!values || !values.length) return `<span class="dim">—</span>`;
    return `<div class="chips">${values.map((d) => `<span class="chip mono">${esc(d)}</span>`).join("")}</div>`;
  };

  const input = (f, value, attrs = "") =>
    `<input class="inp" id="f-${esc(f.name)}" name="${esc(f.name)}" value="${esc(value ?? f.default ?? "")}"${attrs}>`;
  const textArea = (f, value) =>
    `<textarea class="txt" id="f-${esc(f.name)}" name="${esc(f.name)}" rows="3" maxlength="${f.max || 2000}">${esc(value ?? f.default ?? "")}</textarea>`;

  const F = (AC.fields = {});

  F.text = {
    cell: (v) => text(v),
    form: (f, v) => ((f.max || 240) > 120 ? textArea(f, v) : input(f, v, f.max ? ` maxlength="${f.max}"` : "")),
    parse: (f, raw) => String(raw ?? "").trim(),
  };
  F.int = {
    cell: (v) => text(v === null || v === undefined ? v : String(v)),
    form: (f, v) => input(f, v, ` type="number" step="1"${f.min !== undefined && f.min !== null ? ` min="${f.min}"` : ""}${f.max !== undefined && f.max !== null ? ` max="${f.max}"` : ""}`),
    parse: (f, raw) => (String(raw ?? "").trim() === "" ? null : Number.parseInt(String(raw), 10)),
  };
  F.number = {
    cell: (v) => text(v === null || v === undefined ? v : String(v)),
    form: (f, v) => input(f, v, ` type="number" step="any"${f.min !== undefined && f.min !== null ? ` min="${f.min}"` : ""}${f.max !== undefined && f.max !== null ? ` max="${f.max}"` : ""}`),
    parse: (f, raw) => (String(raw ?? "").trim() === "" ? null : Number(String(raw))),
  };
  F.bool = {
    cell: (v) => (v ? `<span class="st st-ok">Yes</span>` : `<span class="dim">No</span>`),
    form: (f, v) => `<label class="check"><input type="checkbox" id="f-${esc(f.name)}" name="${esc(f.name)}"${v ?? f.default ? " checked" : ""}><span>${esc(f.label || f.name)}</span></label>`,
    parse: (f, raw) => !!raw,
  };
  F.date = {
    cell: (v) => text(v ? String(v).slice(0, 10) : v),
    form: (f, v) => input(f, v ? String(v).slice(0, 10) : "", ` type="date"`),
    parse: (f, raw) => String(raw || "").trim() || null,
  };
  F.datetime = {
    cell: (v) => text(v ? AC.fmtDate(v) : v),
    form: (f, v) => input(f, v || "", ` type="datetime-local"`),
    parse: (f, raw) => String(raw || "").trim() || null,
  };
  F.domain = {
    cell: (v) => mono(v),
    form: (f, v) => input(f, v, ` class="inp mono" placeholder="api.example.com" autocomplete="off"`),
    parse: (f, raw) => String(raw ?? "").trim(),
  };
  F.email = {
    cell: (v) => text(v),
    form: (f, v) => input(f, v, ` type="email" autocomplete="off"`),
    parse: (f, raw) => String(raw ?? "").trim(),
  };
  F.url = {
    cell: (v) => (v ? `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a>` : `<span class="dim">—</span>`),
    form: (f, v) => input(f, v, ` type="url" placeholder="https://" autocomplete="off"`),
    parse: (f, raw) => String(raw ?? "").trim(),
  };
  F.enum = {
    cell: (v) => text(v),
    form: (f, v) => `<select class="sel" id="f-${esc(f.name)}" name="${esc(f.name)}">${(f.values || []).map((o) => `<option${(v ?? f.default) === o ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`,
    parse: (f, raw) => String(raw ?? ""),
  };
  F.ref = {
    cell: (v, f, resolve) => {
      const label = resolve ? resolve(v) : v;
      return text(label === null || label === undefined || label === "" ? "—" : label);
    },
    form: (f, v, options = []) =>
      `<select class="sel" id="f-${esc(f.name)}" name="${esc(f.name)}"><option value="">—</option>${options.map((o) => `<option value="${esc(o.id)}"${String(v) === String(o.id) ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`,
    parse: (f, raw) => String(raw || "").trim() || null,
  };

  for (const kind of ["text", "int", "number", "bool", "date", "datetime", "domain", "email", "url"]) {
    F["list<" + kind + ">"] = {
      cell: (v) => (kind === "domain" ? AC.domainChips(v, 3) : listCell(v)),
      form: (f, v) => `<textarea class="txt mono" id="f-${esc(f.name)}" name="${esc(f.name)}" rows="2" placeholder="One per line">${esc(Array.isArray(v) ? v.join("\n") : (v ?? f.default ?? ""))}</textarea><div class="help">One per line, or separated by spaces or commas.</div>`,
      parse: (f, raw) => String(raw ?? "").split(/[\n,\s]+/).map((s) => s.trim()).filter(Boolean),
    };
  }
  F["list<enum>"] = {
    cell: (v) => listCell(v),
    form: (f, v) => `<select class="sel" multiple id="f-${esc(f.name)}" name="${esc(f.name)}">${(f.values || []).map((o) => `<option${(v || []).includes(o) ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`,
    parse: (f, el) => (el && el.selectedOptions ? [...el.selectedOptions].map((o) => o.value) : []),
  };
  F["list<ref>"] = {
    cell: (v, f, resolve) => listCell((v || []).map((id) => (resolve ? resolve(id) : id))),
    form: (f, v, options = []) =>
      `<select class="sel" multiple id="f-${esc(f.name)}" name="${esc(f.name)}">${options.map((o) => `<option value="${esc(o.id)}"${(v || []).map(String).includes(String(o.id)) ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`,
    parse: (f, el) => (el && el.selectedOptions ? [...el.selectedOptions].map((o) => o.value) : []),
  };

  AC.fieldCell = (field, value, resolve) => {
    const r = F[field.type] || F.text;
    return r.cell(value, field, resolve);
  };

  // Generic record form from an entity's fields; ref fields resolve options via lookup(name).
  AC.recordForm = (entity, values = {}, lookup = () => []) => (entity.fields || []).map((f) => {
    const r = F[f.type] || F.text;
    const v = values[f.name];
    if (f.type === "ref" || f.type === "list<ref>") {
      return `<div class="field"><label for="f-${esc(f.name)}">${esc(f.label || f.name)}${f.required ? "" : ` <span class="dim">(optional)</span>`}</label>${r.form(f, v, lookup(f.to))}</div>`;
    }
    if (f.type === "bool") return `<div class="field">${r.form(f, v)}</div>`;
    return `<div class="field"><label for="f-${esc(f.name)}">${esc(f.label || f.name)}${f.required ? "" : ` <span class="dim">(optional)</span>`}</label>${r.form(f, v)}${f.required ? "" : ""}</div>`;
  }).join("");

  // Read a generic record form back into a plain object keyed by field name.
  AC.readForm = (entity, root = document) => {
    const out = {};
    for (const f of entity.fields || []) {
      const el = root.querySelector(`#f-${CSS.escape(f.name)}`);
      if (!el) continue;
      const r = F[f.type] || F.text;
      if (f.type === "bool") out[f.name] = el.checked;
      else if (f.type === "list<enum>" || f.type === "list<ref>") out[f.name] = r.parse(f, el);
      else out[f.name] = r.parse(f, el.value !== undefined ? el.value : el);
    }
    return out;
  };
})();
