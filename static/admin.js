/* schemasmith: generic admin views (queue, report, reference_editor, exports, dashboard, users, wizard). */
(() => {
  const { S, esc, icon } = AC;
  const $ = AC.$;

  const guardRole = (viewType, fn) => {
    const v = AC.viewOf(viewType);
    if (!v) return `<div class="page">${AC.empty("shield", "Not configured", "The active spec has no " + viewType + " view.")}</div>`;
    if (!S.me) return `<div class="page">${AC.authScreen()}</div>`;
    if (!AC.hasRole(S.me, v.roles)) {
      return `<div class="page">${AC.empty("shield", "Not allowed", "Your account does not have a role for this screen.", `<button class="btn btn-fill" data-act="open-auth" data-mode="login">Sign in</button>`)}</div>`;
    }
    return fn(v);
  };
  const adminOnly = (fn) => {
    if (!S.me) return `<div class="page">${AC.authScreen()}</div>`;
    if (!S.me.is_admin) {
      return `<div class="page">${AC.empty("shield", "Admins only", "Your account does not have admin rights. Ask an admin to make you admin.", "")}</div>`;
    }
    return fn();
  };

  const head = (crumb, title, sub, actions = "") =>
    `${AC.crumbs([{ label: "Admin", href: "#/admin" }, { label: crumb }])}<div class="pagehead"><div><h1 class="h1">${esc(title)}</h1><p class="sub">${esc(sub)}</p></div><div class="head-actions">${actions}</div></div>`;

  // Generic where-matcher for dashboard[] and exports[].
  AC.matchWhere = (rec, where) => {
    for (const [k, v] of Object.entries(where || {})) {
      if (k === "state") {
        if (AC.recordState(rec) !== v) return false;
      } else if (v === "<today") {
        const cur = String(rec[k] || "");
        const today = new Date().toISOString().slice(0, 10);
        if (!cur || cur >= today) return false;
      } else if (String(rec[k] ?? "") !== String(v)) return false;
    }
    return true;
  };

  // ---------------------------------------------------------------- dashboard (overview tiles)
  AC.views["admin-overview"] = () => guardRole("dashboard", (v) => {
    const tiles = (AC.spec.dashboard || []).map((d) => {
      const cache = AC.entityCache(d.count);
      const n = (cache.rows && cache.rows.length && cache.rows.filter((r) => AC.matchWhere(r, d.where)).length) || 0;
      void cache;
      return { label: d.label, n };
    });
    const loadMissing = (AC.spec.dashboard || []).map((d) => d.count).filter((name) => !(AC.entityCache(name).rows || []).length);
    if (loadMissing.length && !S.dashLoading) {
      S.dashLoading = true;
      Promise.all(loadMissing.map((name) => AC.loadEntity(name).catch(() => []))).then(() => { S.dashLoading = false; AC.renderPage(); });
    }
    const designer = (AC.spec.schemaEdit || AC.spec.schema_edit)
      ? `<div class="ob-grid" style="margin-bottom:16px"><div class="ob-step"><span class="ob-n">✎</span><div><b>Describe a new use case</b><span>Generate a spec with a local model, review the plan, apply it.</span></div><button class="btn btn-tonal" data-act="design-open">Open designer</button></div></div>` : "";
    return `<div class="page">${head("Overview", v.label || "Overview", "What needs a decision, and how healthy the data is.")}
      ${designer}
      <div class="stats">${tiles.map((t) =>
        `<a class="tile${t.n ? " hot" : ""}" href="#/admin/requests"><span class="tl">${icon("layers")}${esc(t.label)}</span><span class="tv">${t.n}</span></a>`).join("") ||
        AC.empty("layers", "No dashboard tiles", "The active spec defines no dashboard entries.")}</div>
    </div>`;
  });

  // ---------------------------------------------------------------- queue (admin review list + sheet)
  const queueRows = (entity, list, q) => {
    const nameF = entity.fields[0];
    return list.filter((r) => {
      if (!q) return true;
      const hay = [r.owner && r.owner.name, r.owner && r.owner.email, String((nameF && r[nameF.name]) ?? ""),
        (r.lines || []).map((l) => AC.refLabel(l.ref)).join(" ")].join(" ").toLowerCase();
      return hay.includes(q);
    });
  };

  AC.views["admin-requests"] = () => guardRole("queue", (v) => {
    if (!S.loaded.queue) return `<div class="page">${AC.loading()}</div>`;
    const entity = AC.entityOf(v.entity);
    const q = (S.reqQ || "").trim().toLowerCase();
    const wf = AC.spec.workflow;
    const states = (wf && wf.states) || [];
    const counts = {};
    (S.queue || []).forEach((r) => { const s = AC.recordState(r); counts[s] = (counts[s] || 0) + 1; });
    let list = [...(S.queue || [])].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    if (S.reqTab !== "all" && S.reqTab) list = list.filter((r) => AC.recordState(r) === S.reqTab);
    list = queueRows(entity, list, q);
    const tab = (k, label, n) => `<button class="tab${(S.reqTab || "all") === k ? " on" : ""}" role="tab" aria-selected="${(S.reqTab || "all") === k}" data-act="req-tab" data-tab="${k}">${esc(label)}<span class="tc">${n}</span></button>`;
    const rows = list.map((r) => {
      const first = entity.fields[0] ? String(r[entity.fields[0].name] ?? "") : "";
      const lines = (r.lines || []).map((l) => `<span class="chip" title="${esc(AC.lineState(l))}">${esc(AC.refLabel(l.ref))}</span>`).join("");
      return `<tr data-act="review" data-id="${r.id}"><td><div class="who-cell"><span class="avatar">${esc(AC.initials((r.owner && r.owner.name) || "?"))}</span><div style="min-width:0"><div class="nm">${esc((r.owner && r.owner.name) || "")}</div><div class="path">${esc((r.owner && r.owner.email) || "")}</div></div></div></td>` +
        `<td><div class="chips">${lines}</div></td>` +
        `<td class="hide-sm"><div class="clamp" title="${esc(first)}">${esc(first)}</div></td>` +
        `<td class="path" style="white-space:nowrap">${esc(AC.fmtDate(r.created_at))}</td><td>${AC.stPill(AC.recordState(r))}</td></tr>`;
    }).join("");
    return `<div class="page">${head(v.label || "Queue", v.label || "Queue", "Open a record to decide each line, or comment for more context.")}
      <div class="tabs" role="tablist">${tab("all", "All", (S.queue || []).length)}${states.map((s) => tab(s, s.replace(/_/g, " "), counts[s] || 0)).join("")}</div>
      <div class="filterbar"><label class="filter" for="rq-q">${icon("filter")}<input id="rq-q" type="text" autocomplete="off" placeholder="Filter by person, reason or item" value="${esc(S.reqQ || "")}" aria-label="Filter records"></label><span class="count">${list.length} shown</span></div>
      ${list.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Requester</th><th>Items</th><th class="hide-sm">Summary</th><th>Sent</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : AC.empty(q ? "search" : "inbox", q ? "No records match" : "Nothing here yet", q ? "Try a different word." : "New submissions appear here as soon as someone sends one.")}
    </div>`;
  });
  AC.inputs["rq-q"] = (el) => { S.reqQ = el.value; AC.renderPage(); };
  AC.act["req-tab"] = (t) => { S.reqTab = t.dataset.tab; AC.renderPage(); };

  // Review sheet: generic record fields + per-line transition buttons + thread.
  AC.rv = null;
  AC.refreshSheet = () => {
    if (AC.rv && AC.rv.id && !$("#sheet").hidden) AC.reviewSheet();
  };
  AC.reviewSheet = () => {
    const rv = AC.rv;
    const view = AC.viewOf("queue");
    const entity = view && AC.entityOf(view.entity);
    const rec = (S.queue || []).find((x) => x.id === rv.id) || (S.mine || []).find((x) => x.id === rv.id);
    if (!rec || !entity) return;
    const fields = (entity.fields || []).map((f) =>
      `<div class="kv"><span>${esc(f.label || f.name)}</span><span>${AC.fieldText(entity, rec, f)}</span></div>`).join("");
    const lines = Array.isArray(rec.lines) && rec.lines.length
      ? `<div class="lbl" style="margin:18px 0 0">Lines — every row gets decided (${rec.lines.length})</div>` +
        rec.lines.map((l) => {
          const just = l.justification || "";
          return `<div class="rv-item"><div class="info"><span class="nm">${esc(AC.refLabel(l.ref))}</span><span class="path">${esc(AC.lineState(l))}</span>` +
            (just ? `<div class="just-view" style="margin-top:6px"><span class="lbl">Their note</span><div class="quote">${esc(just)}</div></div>` : "") + `</div>` +
            `<div class="seg" role="group" aria-label="Decision">${AC.edgeButtons(entity.name, rec, l)}</div></div>`;
        }).join("")
      : `<div class="lbl" style="margin:18px 0 6px">Decision</div>` + AC.edgeButtons(entity.name, rec, null);
    const list = S.comments[rec.id] || [];
    const thread = list.length
      ? list.map((c) => `<div class="act-row"><span class="avatar sm">${esc(AC.initials(c.author_name))}</span><div style="min-width:0"><b>${esc(c.author_name)}</b>${c.is_admin ? ` <span class="st st-partial">Admin</span>` : ""}<span class="dim"> · ${esc(AC.ago(c.created_at))}</span><div class="quote" style="margin-top:4px">${esc(c.body)}</div></div></div>`).join("")
      : `<p class="sub">No messages yet.</p>`;
    AC.sheet.open(AC.sheetHtml({
      title: "Review",
      body: `<div class="rv-who"><span class="avatar lg">${esc(AC.initials((rec.owner && rec.owner.name) || "?"))}</span><div><div class="nm">${esc((rec.owner && rec.owner.name) || "")}</div><div class="path">${esc((rec.owner && rec.owner.email) || "")} · ${esc(AC.ago(rec.created_at))}</div></div><span class="spacer"></span>${AC.stPill(AC.recordState(rec))}</div>` +
        fields + lines +
        `<div class="lbl" style="margin:14px 0 6px">Discussion ${(S.comments[rec.id] || []).length ? `(${(S.comments[rec.id] || []).length})` : ""}</div>` +
        `<div class="activity" style="margin-bottom:10px">${thread}</div>` +
        `<div class="field"><label for="rv-reply">Reply in thread <span class="dim">(optional)</span></label>` +
        `<textarea class="txt" id="rv-reply" maxlength="2000" rows="2" placeholder="Answer their reply, or note what you decided…"></textarea></div>` +
        `<div class="err" id="e-form"></div>`,
      footer: `<button class="btn btn-text" data-act="sheet-close">Close</button><span class="spacer"></span><button class="btn btn-fill" data-act="rv-reply-send">Send reply</button>`,
    }), { wide: true });
  };
  AC.act.review = async (t) => {
    const id = t.dataset.id;
    try { await AC.loadQueue().catch(() => {}); } catch { /* sheet still opens on stale list */ }
    const rec = (S.queue || []).find((x) => x.id === id);
    if (!rec) return;
    AC.rv = { id };
    AC.reviewSheet();
    try {
      const view = AC.viewOf("queue");
      if (view) { await AC.loadComments(view.entity, id, true); AC.reviewSheet(); }
    } catch { /* thread optional */ }
  };
  AC.act["rv-reply-send"] = async (btn) => {
    const rv = AC.rv;
    const view = AC.viewOf("queue");
    const box = document.getElementById("rv-reply");
    const msg = (box?.value || "").trim();
    if (!msg) return AC.showErr("e-form", "Write the reply first.");
    btn.disabled = true;
    try {
      await AC.postComment(view.entity, rv.id, msg);
      await AC.loadComments(view.entity, rv.id, true);
      AC.reviewSheet();
      AC.toast("Reply sent.");
    } catch (e) { AC.showErr("e-form", e.message); } finally { btn.disabled = false; }
  };

  // ---------------------------------------------------------------- report (reviewer table + CSV)
  AC.views["review"] = () => guardRole("report", (v) => {
    const entity = AC.entityOf(v.entity);
    if (!entity) return `<div class="page">${AC.empty("inbox", "No report entity", "The report view names an unknown entity.")}</div>`;
    if (!S.reportRows && S.reportRows !== 0) return `<div class="page">${AC.loading()}</div>`;
    const rows = (S.reportRows || []).map((r) => {
      const cells = (entity.fields || []).slice(0, 4).map((f) => `<td class="hide-sm">${AC.fieldText(entity, r, f)}</td>`).join("");
      const owner = r.owner ? `<td>${esc(r.owner.name)}<div class="path">${esc(r.owner.email)}</div></td>` : "";
      const first = entity.fields[0] ? String(r[entity.fields[0].name] ?? "") : r.id;
      return `<tr style="cursor:default">${owner}<td><div class="nm">${esc(first.slice(0, 80))}</div></td>${cells}<td>${AC.stPill(AC.recordState(r))}</td></tr>`;
    }).join("");
    const headCells = (entity.fields || []).slice(0, 4).map((f) => `<th class="hide-sm">${esc(f.label || f.name)}</th>`).join("");
    return `<div class="page">${AC.crumbs([{ label: "Review" }])}
      <div class="pagehead"><div><h1 class="h1">${esc(v.label || "Report")}</h1><p class="sub">Every submission, newest decision per row.</p></div>
        <div class="head-actions"><button class="btn btn-tonal" data-act="report-csv" ${(S.reportRows || []).length ? "" : "disabled"}>${icon("download")}CSV</button></div></div>
      ${(S.reportRows || []).length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Person</th><th>Summary</th>${headCells}<th>State</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : AC.empty("inbox", "Nothing to show", "Submissions appear here as soon as someone sends one.")}
    </div>`;
  });
  AC.act["report-csv"] = async () => {
    const view = AC.viewOf("report");
    const entity = view && AC.entityOf(view.entity);
    if (!entity) return;
    const cols = ["person", "email", "state", ...(entity.fields || []).map((f) => f.name)];
    const q = (v) => '"' + String(v ?? "").replace(/"/g, '""') + '"';
    const cell = (r, f) => {
      const v = r[f.name];
      return Array.isArray(v) ? v.join(" ") : v;
    };
    const lines = [cols.join(",")];
    for (const r of S.reportRows || []) {
      lines.push([r.owner && r.owner.name, r.owner && r.owner.email, AC.recordState(r),
        ...(entity.fields || []).map((f) => cell(r, f))].map(q).join(","));
    }
    AC.download(`${entity.name}-report.csv`, "﻿" + lines.join("\n"));
    AC.toast("Downloaded. Opens straight in Excel.");
  };

  // ---------------------------------------------------------------- exports (spec-driven downloads)
  AC.views["admin-allowlist"] = () => guardRole("exports", (v) => {
    const exps = (AC.spec.exports || []).map((x) => `
      <div class="ob-step"><span class="ob-n">⇩</span><div><b>${esc(x.label || x.name)}</b><span>${esc(x.from || "")} · ${(x.formats || []).join(", ")}</span></div><button class="btn btn-tonal" data-act="exp-run" data-name="${esc(x.name)}">${icon("download")}Download</button></div>`).join("");
    return `<div class="page">${head(v.label || "Exports", v.label || "Exports", "Spec-defined extracts. Downloads are built from live records.")}
      <div class="ob-grid">${exps || AC.empty("shield", "No exports", "The active spec defines no exports.")}</div>
      <div class="err" id="e-form" style="margin-top:12px"></div>
    </div>`;
  });
  AC.act["exp-run"] = async (t) => {
    const exp = (AC.spec.exports || []).find((x) => x.name === t.dataset.name);
    if (!exp) return;
    t.disabled = true;
    try {
      const ext = (exp.formats || [])[0] || "csv";
      await AC.downloadFile(`/api/exports/${encodeURIComponent(exp.name)}.${ext}`, `${exp.name}.${ext}`);
      AC.toast(`Downloaded ${exp.name}.`);
    } catch (e) {
      try {
        const text = await AC.runExport(exp);
        AC.download(`${exp.name}.csv`, text, "text/csv");
        AC.toast(`Downloaded ${exp.name} (computed locally).`);
      } catch (e2) { AC.fail(e2); }
    } finally { t.disabled = false; }
  };
  // Generic export runner: "record.lines.ref.field" with optional state filter,
  // or a bare reference-entity name served by /api/e export.csv.
  AC.runExport = async (exp) => {
    const from = String(exp.from || "");
    if (!from.includes(".")) {
      return AC.api(`/api/e/${encodeURIComponent(from)}/export.csv`);
    }
    const parts = from.split(".");
    const recName = parts[0];
    let rows = (AC.entityCache(recName).rows || []).slice();
    if (!rows.length) {
      const res = await AC.api(`/api/e/${encodeURIComponent(recName)}`);
      rows = (res && res.items) || [];
      AC.entityCache(recName).rows = rows;
    }
    rows = rows.filter((r) => AC.matchWhere(r, exp.where));
    const values = [];
    if (parts[1] === "lines") {
      const field = parts[parts.length - 1];
      for (const r of rows) {
        for (const line of r.lines || []) {
          if (exp.where && exp.where.state && AC.lineState(line) !== exp.where.state) continue;
          let node = line.ref || {};
          for (const p of parts.slice(3, -1)) node = (node && node[p]) || {};
          const v = node ? node[field] : null;
          if (Array.isArray(v)) values.push(...v);
          else if (v !== null && v !== undefined && v !== "") values.push(String(v));
        }
      }
    } else {
      const field = parts[parts.length - 1];
      for (const r of rows) {
        const v = r[field];
        if (Array.isArray(v)) values.push(...v);
        else if (v !== null && v !== undefined && v !== "") values.push(String(v));
      }
    }
    const uniq = exp.distinct ? [...new Set(values)] : values;
    const fmt = (exp.formats || [])[0] || "csv";
    if (fmt === "txt") return uniq.join("\n") + (uniq.length ? "\n" : "");
    return uniq.map((v) => `"${String(v).replace(/"/g, '""')}"`).join("\n") + (uniq.length ? "\n" : "");
  };

  // ---------------------------------------------------------------- reference_editor (generic catalog manager)
  S.cmSel = null;
  S.cmClosed = S.cmClosed || new Set();
  S.cmEntity = null;
  const cmEntity = () => {
    const v = AC.viewOf("reference_editor");
    const names = (v && v.entities) || [];
    if (!S.cmEntity || !names.includes(S.cmEntity)) S.cmEntity = names[0] || null;
    return (S.cmEntity && AC.entityOf(S.cmEntity)) || null;
  };
  const cmCache = () => (S.cmEntity ? AC.entityCache(S.cmEntity) : { rows: [], byId: {}, roots: [], nodes: {} });
  const flatTopics = (nodes, depth = 0) =>
    (nodes || []).flatMap((n) => [{ n, depth }, ...flatTopics(n.children, depth + 1)]);
  const topicOptions = (selected, { none = false } = {}) =>
    (none ? `<option value="">Top level</option>` : "") +
    flatTopics(cmCache().roots).map((t) => `<option value="${t.n.id}"${t.n.id === selected ? " selected" : ""}>${" ".repeat(t.depth)}${esc(t.n.name || AC.refLabel(t.n))}</option>`).join("");

  AC.views["admin-catalog"] = () => guardRole("reference_editor", (v) => {
    const entity = cmEntity();
    if (!entity) return `<div class="page">${AC.empty("folder", "No reference entities", "The active spec defines no editable reference entities.")}</div>`;
    const tabs = (v.entities || []).map((name) =>
      `<button class="tab${S.cmEntity === name ? " on" : ""}" role="tab" aria-selected="${S.cmEntity === name}" data-act="cm-entity" data-name="${esc(name)}">${esc((AC.entityOf(name) && (AC.entityOf(name).label || name)) || name)}</button>`).join("");
    const cache = cmCache();
    const ent = AC.entityOf(S.cmEntity);
    const hasTree = !!(ent && (ent.hierarchy || ent.parent));
    const main = hasTree ? cmTreeMain(entity) : cmFlatMain(entity);
    return `<div class="page">${head(v.label || "Reference data", v.label || "Reference data", "Add, edit and reorder " + (entity.label || entity.name) + " records.")}
      <div class="tabs" role="tablist">${tabs}</div>
      <div class="head-actions" style="margin-bottom:12px"><button class="btn btn-outline" data-act="cm-import">${icon("upload")}Import CSV</button>
        <button class="btn btn-outline" data-act="cm-export">${icon("download")}Export CSV</button>
        <button class="btn btn-tonal" data-act="cm-add">${icon("plus")}Add</button></div>
      ${hasTree ? `<div class="cm"><div class="cm-tree"><div class="cm-th"><span>Topics</span><span class="count">${(cache.roots || []).length}</span></div>${(cache.roots || []).map((n) => cmTopicRow(n, 0)).join("")}</div><div class="cm-main">${main}</div></div>` : main}
    </div>`;
  });
  const cmTopicRow = (n, depth) => {
    const kids = (n.children || []).length, open = !S.cmClosed.has(n.id);
    return `<div class="tr-row${S.cmSel === n.id ? " on" : ""}" data-id="${n.id}" data-act="cm-select" style="margin-left:${depth * 16}px">
      <span class="nm">${icon(AC.iconOr(n.icon))}<span style="overflow:hidden;text-overflow:ellipsis">${esc(n.name || AC.refLabel(n))}</span></span>
      <span class="row-actions"><button class="iconbtn sm" data-act="cm-edit-topic" data-id="${n.id}" title="Edit" aria-label="Edit ${esc(n.name || "")}">${icon("edit")}</button>
        <button class="iconbtn sm danger" data-act="cm-del" data-id="${n.id}" data-kind="topic" title="Delete" aria-label="Delete ${esc(n.name || "")}">${icon("trash")}</button></span>
      <span class="nbadge">${n._count}</span></div>` + (kids && open ? n.children.map((c) => cmTopicRow(c, depth + 1)).join("") : "");
  };
  const cmRowCells = (entity, it) =>
    (entity.fields || []).slice(0, 4).map((f) => `<td class="hide-sm">${AC.fieldText(entity, it, f)}</td>`).join("");
  const cmTreeMain = (entity) => {
    const cache = cmCache();
    if (!S.cmSel || !cache.nodes[S.cmSel]) S.cmSel = cache.roots[0] ? cache.roots[0].id : null;
    const node = S.cmSel ? cache.nodes[S.cmSel] : null;
    if (!node) return AC.empty("folder", "No topics yet", "Add a topic first.", `<button class="btn btn-fill" data-act="cm-add-topic">${icon("plus")}Add topic</button>`);
    const headCells = (entity.fields || []).slice(0, 4).map((f) => `<th class="hide-sm">${esc(f.label || f.name)}</th>`).join("");
    const rows = (node.items || []).map((it) => `<tr class="it-row" data-act="cm-edit-item" data-id="${it.id}">
      <td><div class="nm">${esc(AC.refLabel(it))}</div></td>${cmRowCells(entity, it)}
      <td class="c-act"><span class="row-actions" style="justify-content:flex-end">
        <button class="iconbtn sm" data-act="cm-move" data-id="${it.id}" data-dir="-1" title="Move up" aria-label="Move up">${icon("arrow-up")}</button>
        <button class="iconbtn sm" data-act="cm-move" data-id="${it.id}" data-dir="1" title="Move down" aria-label="Move down">${icon("arrow-down")}</button>
        <button class="iconbtn sm danger" data-act="cm-del" data-id="${it.id}" data-kind="item" title="Delete" aria-label="Delete">${icon("trash")}</button></span></td></tr>`).join("");
    return `<div class="pagehead" style="margin-bottom:12px"><div><h2 class="h1" style="font-size:18px">${esc(node.name || "")}</h2>
        <p class="sub">${(node.items || []).length} item(s) here.</p></div>
        <div class="head-actions"><button class="btn btn-fill" data-act="cm-add-item" data-id="${node.id}">${icon("plus")}Add item</button></div></div>` +
      (rows ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Name</th>${headCells}<th></th></tr></thead><tbody>${rows}</tbody></table></div>`
        : AC.empty("package", "No items in this topic", "Add the first item.", `<button class="btn btn-fill" data-act="cm-add-item" data-id="${node.id}">${icon("plus")}Add item</button>`));
  };
  const cmFlatMain = (entity) => {
    const cache = cmCache();
    const headCells = (entity.fields || []).slice(0, 4).map((f) => `<th class="hide-sm">${esc(f.label || f.name)}</th>`).join("");
    const rows = (cache.rows || []).map((it) => `<tr class="it-row" data-act="cm-edit-item" data-id="${it.id}">
      <td><div class="nm">${esc(AC.refLabel(it))}</div></td>${cmRowCells(entity, it)}
      <td class="c-act"><span class="row-actions" style="justify-content:flex-end">
        <button class="iconbtn sm danger" data-act="cm-del" data-id="${it.id}" data-kind="item" title="Delete" aria-label="Delete">${icon("trash")}</button></span></td></tr>`).join("");
    return rows ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Name</th>${headCells}<th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : AC.empty("package", "No records yet", "Add the first one.", `<button class="btn btn-fill" data-act="cm-add">${icon("plus")}Add</button>`);
  };

  AC.act["cm-entity"] = (t) => { S.cmEntity = t.dataset.name; S.cmSel = null; AC.renderPage(); };
  AC.act["cm-select"] = (t) => { S.cmSel = t.dataset.id; AC.renderPage(); };
  AC.act["cm-add"] = () => cmItemSheet(null, null);
  AC.act["cm-add-item"] = (t) => cmItemSheet(null, t.dataset.id);
  AC.act["cm-add-topic"] = () => cmTopicSheet(null);
  AC.act["cm-edit-item"] = (t) => { if (t.target.closest("[data-act='cm-del'],[data-act='cm-move']")) return; cmItemSheet(t.dataset.id, null); };
  AC.act["cm-edit-topic"] = (t) => cmTopicSheet(t.dataset.id);
  AC.act["cm-del"] = async (t) => {
    if (!confirm("Delete this record? Records used in past submissions are archived instead.")) return;
    await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}/${encodeURIComponent(t.dataset.id)}`, { method: "DELETE" });
    await AC.loadEntity(S.cmEntity);
    AC.render();
    AC.toast("Deleted.");
  };
  AC.act["cm-move"] = async (t) => {
    const cache = cmCache();
    const node = cache.nodes[S.cmSel];
    if (!node) return;
    const ids = node.items.map((i) => i.id);
    const i = ids.indexOf(t.dataset.id), j = i + Number(t.dataset.dir);
    if (j < 0 || j >= ids.length) return AC.toast(Number(t.dataset.dir) < 0 ? "Already first." : "Already last.");
    [ids[i], ids[j]] = [ids[j], ids[i]];
    await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}/reorder`, { method: "POST", body: { ids } });
    await AC.loadEntity(S.cmEntity);
    AC.render();
  };
  const cmReload = async (msg) => { await AC.loadEntity(S.cmEntity); AC.render(); if (msg) AC.toast(msg); };

  let itf = null;
  const cmItemSheet = (id, parentId) => {
    const entity = cmEntity();
    const cache = cmCache();
    const row = id ? cache.byId[id] : null;
    const ent = AC.entityOf(S.cmEntity);
    const needsParent = !!(ent && (ent.parent || ent.hierarchy) && !cache.roots.length) || (ent && ent.parent && !id && !parentId && !S.cmSel);
    itf = { id, parent: (row && row.parent_id) || parentId || S.cmSel || "", values: row ? { ...row } : {} };
    const parentSel = ent && (ent.parent || ent.hierarchy)
      ? `<div class="field"><label for="if-parent">Inside</label><select class="sel" id="if-parent">${ent.parent ? "" : `<option value="">Top level</option>`}${topicOptions(itf.parent)}</select></div>` : "";
    void needsParent;
    AC.sheet.open(AC.sheetHtml({
      title: id ? "Edit" : "Add",
      body: `<div class="err" id="e-form"></div>${parentSel}` +
        AC.recordForm(entity, itf.values, (name) => AC.refOptions(name)),
      footer: `<span class="spacer"></span><button class="btn btn-text" data-act="sheet-close">Cancel</button><button class="btn btn-fill" data-act="if-save">Save</button>`,
    }));
  };
  AC.act["if-save"] = async () => {
    const entity = cmEntity();
    const data = AC.readForm(entity);
    const gp = document.getElementById("if-parent");
    if (gp) data.parent_id = gp.value || null;
    if (Object.keys(data).length === 1 && data.parent_id === undefined) return AC.showErr("e-form", "Nothing to save.");
    try {
      if (itf.id) await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}/${encodeURIComponent(itf.id)}`, { method: "PATCH", body: data });
      else {
        const ent = AC.entityOf(S.cmEntity);
        if ((ent.parent || ent.hierarchy) && !data.parent_id && !ent.parent) data.parent_id = null;
        await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}`, { method: "POST", body: data });
      }
      AC.sheet.close();
      await cmReload("Saved.");
    } catch (e) { AC.showErr("e-form", e.message); }
  };
  let tf = null;
  const cmTopicSheet = (id) => {
    const cache = cmCache();
    const row = id ? cache.nodes[id] : null;
    tf = { id, name: (row && (row.name || "")) || "" };
    AC.sheet.open(AC.sheetHtml({
      title: id ? "Edit topic" : "Add topic",
      body: `<div class="err" id="e-form"></div>
        <div class="field"><label for="tf-name">Name</label><input class="inp" id="tf-name" maxlength="80" autocomplete="off" value="${esc(tf.name)}"></div>
        <div class="field"><label for="tf-parent">Inside</label><select class="sel" id="tf-parent"><option value="">Top level</option>${topicOptions(row && row.parent ? row.parent.id : null)}</select></div>`,
      footer: `<span class="spacer"></span><button class="btn btn-text" data-act="sheet-close">Cancel</button><button class="btn btn-fill" data-act="tf-save">Save</button>`,
    }));
  };
  AC.act["tf-save"] = async () => {
    const name = (document.getElementById("tf-name").value || "").trim();
    const parent = document.getElementById("tf-parent").value || null;
    if (!name) return AC.showErr("e-form", "Give the topic a name.");
    try {
      if (tf.id) await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}/${encodeURIComponent(tf.id)}`, { method: "PATCH", body: { name, parent_id: parent } });
      else await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}`, { method: "POST", body: { name, parent_id: parent } });
      AC.sheet.close();
      await cmReload("Topic saved.");
    } catch (e) { AC.showErr("e-form", e.message); }
  };
  AC.act["cm-export"] = async () => {
    AC.download(`${S.cmEntity}.csv`, await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}/export.csv`));
  };
  AC.act["cm-import"] = () => {
    AC.sheet.open(AC.sheetHtml({
      title: "Import CSV",
      body: `<p class="sub" style="margin:0 0 14px">Columns match the reference entity fields. Existing records are matched by id and updated; the rest are created.</p>
        <div class="field"><label for="imp-text">Paste CSV here</label><textarea class="txt mono" id="imp-text" rows="9" spellcheck="false"></textarea></div>
        <div id="imp-res"></div>`,
      footer: `<button class="btn btn-text" data-act="sheet-close">Close</button><span class="spacer"></span><button class="btn btn-outline" data-act="imp-run" data-dry="1">Preview</button><button class="btn btn-fill" data-act="imp-run">Import</button>`,
    }), { wide: true });
  };
  AC.act["imp-run"] = async (t) => {
    const text = $("#imp-text").value;
    const box = $("#imp-res");
    if (!text.trim()) { box.innerHTML = `<div class="res err">Paste some CSV first.</div>`; return; }
    const dry = !!t.dataset.dry;
    try {
      const r = await AC.api(`/api/e/${encodeURIComponent(S.cmEntity)}/import.csv`, { method: "POST", body: { csv: text, dry_run: dry } });
      box.innerHTML = `<div class="res"><div class="kv"><span>${dry ? "Preview" : "Imported"}:</span><span><b>${r.created}</b> created</span><span><b>${r.updated}</b> updated</span></div>` +
        (r.errors.length ? `<ul>${r.errors.slice(0, 20).map((x) => `<li>Row ${x.row}: ${esc(x.message)}</li>`).join("")}</ul>${r.errors.length > 20 ? `<div class="help">and ${r.errors.length - 20} more</div>` : ""}</div>` : "") + `</div>`;
      if (!dry) await cmReload(`Import finished: ${r.created} created, ${r.updated} updated.`);
    } catch (e) { box.innerHTML = `<div class="res err">${esc(e.message)}</div>`; }
  };

  // ---------------------------------------------------------------- users (engine accounts, not domain data)
  AC.views["admin-users"] = () => adminOnly(() => {
    if (!S.loaded.users) return `<div class="page">${AC.loading()}</div>`;
    const q = (S.userQ || "").trim().toLowerCase();
    const all = S.users;
    const pend = all.filter((u) => u.status === "pending");
    const appr = all.filter((u) => u.status === "approved");
    const rej = all.filter((u) => u.status === "rejected");
    let list = S.userTab === "pending" ? pend : S.userTab === "approved" ? appr : S.userTab === "rejected" ? rej : all;
    if (q) list = list.filter((u) => (u.name + " " + u.email).toLowerCase().includes(q));
    const hasReviewer = (AC.spec.roles || []).includes("reviewer");
    const tab = (k, label, n) => `<button class="tab${S.userTab === k ? " on" : ""}" role="tab" aria-selected="${S.userTab === k}" data-act="user-tab" data-tab="${k}">${label}<span class="tc">${n}</span></button>`;
    const whoCell = (u) => `<div class="who-cell"><span class="avatar">${esc(AC.initials(u.name))}</span><div style="min-width:0"><div class="nm">${esc(u.name)}</div><div class="path">${esc(u.email)}${u.signup_ip ? ` · <span class="mono">IP ${esc(u.signup_ip)}</span>` : ""}</div></div></div>`;
    const rows = list.map((u) => `<tr style="cursor:default"><td>${whoCell(u)}</td>
      <td class="hide-sm path" style="white-space:nowrap">${esc(AC.ago(u.created_at))}</td>
      <td>${AC.userStPill(u.status)}${u.is_admin ? ` <span class="st st-partial">Admin</span>` : ""}${u.is_reviewer ? ` <span class="st st-warn">Reviewer</span>` : ""}</td>
      <td class="c-act"><span class="row-actions" style="display:flex;justify-content:flex-end;gap:6px">
        ${u.status === "pending" ? `<button class="btn btn-fill" data-act="user-approve" data-id="${u.id}">Approve</button><button class="btn btn-outline" data-act="user-reject" data-id="${u.id}">Reject</button>` : ""}
        ${u.status !== "pending" ? `<button class="btn btn-outline" data-act="user-admin" data-id="${u.id}">${u.is_admin ? "Remove admin" : "Make admin"}</button>` : ""}
        ${hasReviewer && u.status === "approved" && !u.is_admin ? `<button class="btn btn-tonal" data-act="user-reviewer" data-id="${u.id}">${u.is_reviewer ? "Remove reviewer" : "Assign reviewer"}</button>` : ""}
        ${u.status === "rejected" ? `<button class="btn btn-tonal" data-act="user-approve" data-id="${u.id}">Approve</button>` : ""}
      </span></td></tr>`).join("");
    const allowedRows = (S.allowed || []).map((a) => `<div class="allow-row"><span class="mono">${esc(a.email)}</span><span class="dim">by ${esc(a.added_by || "admin")}</span><span class="spacer"></span><button class="iconbtn sm danger" data-act="allow-del" data-email="${esc(a.email)}" title="Remove" aria-label="Remove ${esc(a.email)}">${icon("trash")}</button></div>`).join("");
    return `<div class="page">${head("Users", "Users", "Approve new accounts, or pre-approve emails so those sign-ups get in instantly.", `<button class="btn btn-fill" data-act="allow-add-open">${icon("plus")}Pre-approve email</button>`)}
      <div class="ob-grid" style="margin-bottom:16px"><div class="ob-step"><span class="ob-n">✉</span><div><b>How it works</b><span>1) Pre-approve work emails below. 2) Teammates sign up. 3) Pre-approved go straight in; the rest wait here for one click.</span></div></div></div>
      <h2 class="h2">Pre-approved emails (${(S.allowed || []).length})</h2>
      <div class="field" style="max-width:520px"><label for="allow-email">Add an email that should skip approval</label>
        <div style="display:flex;gap:8px"><input class="inp" id="allow-email" type="email" placeholder="teammate@example.com" autocomplete="off"><button class="btn btn-tonal" data-act="allow-add">Add</button></div><div class="err" id="e-allow"></div></div>
      ${(S.allowed || []).length ? `<div class="allow-list">${allowedRows}</div>` : `<p class="sub">None yet. Add your team's emails — first account is already admin.</p>`}
      <h2 class="h2" style="margin-top:24px">Accounts</h2>
      <div class="tabs" role="tablist">${tab("pending", "Waiting", pend.length)}${tab("approved", "Approved", appr.length)}${tab("rejected", "Not approved", rej.length)}${tab("all", "All", all.length)}</div>
      <div class="filterbar"><label class="filter" for="uq">${icon("filter")}<input id="uq" type="text" autocomplete="off" placeholder="Filter by name or email" value="${esc(S.userQ || "")}" aria-label="Filter users"></label><span class="count">${list.length} shown</span></div>
      ${list.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Person</th><th class="hide-sm">Signed up</th><th>Status</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
        : AC.empty("user", q ? "No accounts match" : "Nobody is waiting", q ? "Try a different word." : "New sign-ups appear here.")}
    </div>`;
  });
  AC.inputs.uq = (el) => { S.userQ = el.value; AC.renderPage(); };
  AC.act["user-tab"] = (t) => { S.userTab = t.dataset.tab; AC.renderPage(); };
  AC.act["user-approve"] = async (t) => {
    await AC.api(`/api/admin/users/${t.dataset.id}/approve`, { method: "POST" });
    await AC.loadUsers(); AC.render();
    AC.toast("Account approved. They can sign in now.");
  };
  AC.act["user-reject"] = async (t) => {
    if (!confirm("Reject this account? They will not be able to sign in.")) return;
    await AC.api(`/api/admin/users/${t.dataset.id}/reject`, { method: "POST" });
    await AC.loadUsers(); AC.render();
    AC.toast("Account rejected.");
  };
  AC.act["user-admin"] = async (t) => {
    const u = S.users.find((x) => x.id === t.dataset.id);
    if (!u) return;
    try {
      await AC.api(`/api/admin/users/${u.id}/admin`, { method: "POST", body: { is_admin: !u.is_admin } });
      await AC.loadUsers(); AC.render();
      AC.toast(u.is_admin ? `${u.name} is no longer admin.` : `${u.name} is now admin.`);
    } catch (e) { AC.fail(e); }
  };
  AC.act["user-reviewer"] = async (t) => {
    const u = S.users.find((x) => x.id === t.dataset.id);
    if (!u) return;
    try {
      await AC.api(`/api/admin/users/${u.id}/reviewer`, { method: "POST", body: { is_reviewer: !u.is_reviewer } });
      await AC.loadUsers(); AC.render();
      AC.toast(u.is_reviewer ? `${u.name} is no longer a reviewer.` : `${u.name} can now review reports.`);
    } catch (e) { AC.fail(e); }
  };
  AC.act["allow-add"] = async () => {
    const el = document.getElementById("allow-email");
    const box = document.getElementById("e-allow");
    const email = (el?.value || "").trim();
    if (!/.+@.+\..+/.test(email)) { if (box) box.textContent = "Enter a valid email."; return; }
    try {
      await AC.api("/api/admin/allowed-emails", { method: "POST", body: { email } });
      el.value = "";
      if (box) box.textContent = "";
      await AC.loadUsers(); AC.render();
      AC.toast(`${email} pre-approved.`);
    } catch (e) { if (box) box.textContent = e.message; else AC.fail(e); }
  };
  AC.act["allow-add-open"] = () => { const el = document.getElementById("allow-email"); if (el) { el.focus(); el.scrollIntoView({ block: "center" }); } };
  AC.act["allow-del"] = async (t) => {
    await AC.api(`/api/admin/allowed-emails/${encodeURIComponent(t.dataset.email)}`, { method: "DELETE" });
    await AC.loadUsers(); AC.render();
    AC.toast(`Removed ${t.dataset.email} from pre-approved.`);
  };

  // ---------------------------------------------------------------- designer wizard (LLM routes)
  S.dz = S.dz || null;
  AC.act["design-open"] = () => {
    S.dz = { step: "describe", useCase: "", result: null, plan: null, error: "" };
    AC.designSheet();
  };
  AC.designSheet = () => {
    const dz = S.dz;
    let body = "";
    if (dz.step === "describe") {
      body = `<div class="field"><label for="dz-use">What should people request, track or approve?</label>` +
        `<textarea class="txt" id="dz-use" rows="4" maxlength="2000" placeholder="e.g. Developers request access to cloud tools; an admin approves each line…">${esc(dz.useCase)}</textarea><div class="err" id="e-form">${esc(dz.error || "")}</div></div>`;
    } else if (dz.step === "summary" && dz.result) {
      const spec = dz.result.spec;
      const ents = (spec.entities || []).map((e) => `<span class="chip">${esc(e.name)} <span class="dim">${esc(e.kind)}</span></span>`).join("");
      const states = spec.workflow ? (spec.workflow.states || []).map((s) => `<span class="chip mono">${esc(s)}</span>`).join("") : `<span class="dim">no workflow</span>`;
      const views = (spec.views || []).map((v) => `<span class="chip mono">${esc(v.type)}:${esc(v.entity || (v.entities || []).join("+"))}</span>`).join("");
      body = `<div class="lbl">Entities</div><div class="chips" style="margin-bottom:10px">${ents}</div>` +
        `<div class="lbl">States</div><div class="chips" style="margin-bottom:10px">${states}</div>` +
        `<div class="lbl">Views</div><div class="chips" style="margin-bottom:10px">${views}</div>` +
        `<div class="field"><label for="dz-json">Spec JSON <span class="dim">(editable — re-plan before applying)</span></label>` +
        `<textarea class="txt mono" id="dz-json" rows="10" spellcheck="false">${esc(JSON.stringify(spec, null, 2))}</textarea></div><div class="err" id="e-form">${esc(dz.error || "")}</div>`;
    } else if (dz.step === "plan" && dz.plan) {
      const steps = (dz.plan.sql || []).map((s) =>
        `<div class="quote mono" style="margin-bottom:8px">${esc(s.sql)}<div class="dim">params: ${esc(JSON.stringify(s.parameters ?? []))}</div></div>`).join("");
      const impact = (dz.plan.operations || []).map((o) => `<span class="chip mono">${esc(o.kind)} · ${esc(o.classification)}</span>`).join("");
      body = `<div class="lbl">Impact</div><div class="chips" style="margin-bottom:10px">${impact || `<span class="dim">no changes</span>`}</div>` +
        `<div class="lbl">Migration SQL (planned, not applied)</div><div style="margin-bottom:10px">${steps || `<span class="dim">nothing to migrate</span>`}</div>` +
        `<p class="sub">Back up the database before applying. Destructive changes are archived, never dropped.</p>` +
        `<label class="check"><input type="checkbox" id="dz-confirm"><span>I have a backup and I want to apply this plan.</span></label><div class="err" id="e-form">${esc(dz.error || "")}</div>`;
    }
    const footerByStep = {
      describe: `<button class="btn btn-text" data-act="sheet-close">Cancel</button><span class="spacer"></span><button class="btn btn-fill" data-act="dz-generate">Generate</button>`,
      summary: `<button class="btn btn-text" data-act="sheet-close">Cancel</button><span class="spacer"></span><button class="btn btn-outline" data-act="dz-generate">Regenerate</button><button class="btn btn-fill" data-act="dz-plan">Request plan</button>`,
      plan: `<button class="btn btn-text" data-act="sheet-close">Cancel</button><span class="spacer"></span><button class="btn btn-fill" data-act="dz-apply">Apply plan</button>`,
    };
    AC.sheet.open(AC.sheetHtml({
      title: "Review the design",
      body,
      footer: footerByStep[dz.step] || footerByStep.describe,
    }), { wide: true });
  };
  // Who may open the designer: admins, and guests only when the server allows it.
  AC.canDesign = () => !!(S.me && AC.schemaEdit && (S.me.is_admin || (S.me.is_guest && AC.guestDesigner)));

  // Bring-your-own-key. The key lives in this browser only: sessionStorage by default,
  // localStorage when the user ticks "remember". It is sent per request, never saved server-side.
  const KEY = "ac.openai_key";
  const keyGet = () => {
    try { const v = sessionStorage.getItem(KEY); if (v) return v; } catch { /* storage may be blocked */ }
    try { return localStorage.getItem(KEY) || ""; } catch { return ""; }
  };
  const keyIsRemembered = () => { try { return !!localStorage.getItem(KEY); } catch { return false; } };
  const keySet = (value, remember) => {
    try { sessionStorage.removeItem(KEY); } catch { /* ignore */ }
    try { localStorage.removeItem(KEY); } catch { /* ignore */ }
    if (!value) return;
    try { (remember ? localStorage : sessionStorage).setItem(KEY, value); } catch { /* storage may be blocked */ }
  };
  AC.keyHeaders = () => { const k = keyGet(); return k ? { "X-OpenAI-Key": k } : {}; };

  // Prompt-first screen: the headline feature. Also the empty-install landing page.
  AC.views.design = () => {
    if (!S.me) return AC.authScreen();
    if (!AC.canDesign()) {
      const why = !AC.schemaEdit ? "The designer is switched off on this server (SCHEMA_EDIT=0)."
        : S.me.is_guest ? "The designer is not available to guests on this server." : "Only admins can use the designer.";
      return `<div class="page">${AC.empty("shield", "Designer unavailable", why)}</div>`;
    }
    const dz = S.dz || {};
    return `<div class="page dp-wrap">
      <h1 class="h1">Describe your use case</h1>
      <p class="sub">AI designs the database and the app builds itself. You review the plan before anything is applied.</p>
      <div class="field"><label for="dp-use">What should people request, track or approve?</label>
        <textarea class="txt dp-use" id="dp-use" rows="7" maxlength="2000" placeholder="e.g. Our media lab lends equipment. Borrowers request an asset with a due date; an admin approves it, records checkout and return, and exports overdue items.">${esc(dz.useCase || "")}</textarea></div>
      <div class="field"><label for="dp-key">OpenAI API key</label>
        <input class="inp mono" id="dp-key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-..." value="${esc(keyGet())}"></div>
      <label class="check"><input type="checkbox" id="dp-rem"${keyIsRemembered() ? " checked" : ""}><span>Remember on this device</span></label>
      <p class="help">Your key stays in this browser and is sent only to OpenAI, for this request. The server never stores or logs it.${S.me.is_admin ? " Leave it empty to use the model configured on the server." : ""}</p>
      <div class="err" id="e-form">${esc(dz.error || "")}</div>
      <div style="margin-top:12px"><button class="btn btn-fill" data-act="dp-generate">Generate</button></div>
    </div>`;
  };
  AC.act["dp-generate"] = async (btn) => {
    const g = (id) => document.getElementById(id);
    const useCase = (g("dp-use").value || "").trim();
    const key = (g("dp-key").value || "").trim();
    if (!useCase) return AC.showErr("e-form", "Describe the use case first.");
    if (!key && !(S.me && S.me.is_admin)) return AC.showErr("e-form", "Add your OpenAI API key to generate.");
    keySet(key, g("dp-rem").checked);
    S.dz = { step: "describe", useCase, result: null, plan: null, error: "" };
    btn.disabled = true;
    btn.textContent = "Generating...";
    try {
      const res = await AC.api("/api/spec/generate", { method: "POST", headers: AC.keyHeaders(), body: { use_case: useCase, current_spec: null, mode: "create" } });
      S.dz = { step: "summary", useCase, result: res, plan: null, error: "" };
      AC.designSheet();
    } catch (e) { S.dz.error = dzFail(e); AC.showErr("e-form", S.dz.error); }
    finally { btn.disabled = false; btn.textContent = "Generate"; }
  };
  const dzFail = (e) => {
    // 503 + not_configured is the expected no-model state: say how to fix it.
    if (e && (e.status === 503 || /not_configured|configure/i.test(e.message || ""))) {
      return "No model is available. Add your OpenAI API key, or set LLM_MODEL on the server (for example a local Ollama model). Everything else keeps working offline.";
    }
    return (e && e.message) || "Generation failed.";
  };
  AC.act["dz-generate"] = async (btn) => {
    const dz = S.dz;
    const ta = document.getElementById("dz-use") || document.getElementById("dz-json");
    if (ta && ta.id === "dz-use") dz.useCase = ta.value;
    if (!dz.useCase.trim() && dz.step === "describe") { AC.showErr("e-form", "Describe the use case first."); return; }
    btn.disabled = true;
    try {
      const current = (dz.step === "summary" && document.getElementById("dz-json"))
        ? JSON.parse(document.getElementById("dz-json").value)
        : null;
      const res = await AC.api("/api/spec/generate", {
        method: "POST",
        headers: AC.keyHeaders(),
        body: current ? { use_case: dz.useCase, current_spec: current, mode: "revise" } : { use_case: dz.useCase, current_spec: null, mode: "create" },
      });
      S.dz = { step: "summary", useCase: dz.useCase, result: res, plan: null, error: "" };
      AC.designSheet();
    } catch (e) {
      if (e instanceof SyntaxError) AC.showErr("e-form", "That JSON does not parse. Fix it and try again.");
      else { dz.error = dzFail(e); AC.designSheet(); }
    } finally { btn.disabled = false; }
  };
  AC.act["dz-plan"] = async (btn) => {
    const dz = S.dz;
    let spec = dz.result && dz.result.spec;
    try {
      const raw = document.getElementById("dz-json");
      if (raw && raw.value.trim()) spec = JSON.parse(raw.value);
    } catch { AC.showErr("e-form", "That JSON does not parse. Fix it and try again."); return; }
    btn.disabled = true;
    try {
      const plan = await AC.api("/api/spec/plan", { method: "POST", body: { spec } });
      dz.result = { spec };
      dz.plan = plan;
      dz.step = "plan";
      dz.error = "";
      AC.designSheet();
    } catch (e) { dz.error = dzFail(e); AC.designSheet(); } finally { btn.disabled = false; }
  };
  AC.act["dz-apply"] = async (btn) => {
    const dz = S.dz;
    if (!document.getElementById("dz-confirm").checked) { AC.showErr("e-form", "Confirm the backup checkbox first."); return; }
    btn.disabled = true;
    try {
      await AC.api("/api/spec/apply", {
        method: "POST",
        body: { spec: dz.result.spec, plan_hash: dz.plan.plan_hash, confirm_risky: false, confirm_destructive: null },
      });
      AC.sheet.close();
      S.dz = null;
      await AC.loadSpec().catch(() => null);
      AC.applyBrand();
      S.loaded.catalog = false;
      S.loaded.queue = false;
      await Promise.all([AC.loadBrowseData().catch(() => {}), AC.loadMine().catch(() => {}), AC.loadQueue().catch(() => {})]);
      AC.render();
      if (S.route.name === "design") location.hash = "#/catalog";
      AC.render();
      AC.toast("New spec applied. Navigation rebuilt.");
    } catch (e) { dz.error = dzFail(e); AC.designSheet(); } finally { btn.disabled = false; }
  };
})();
