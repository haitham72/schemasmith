/* schemasmith: generic requester views (browse_pick submit + my_records). No domain literals. */
(() => {
  const { S, esc, icon } = AC;

  // ---------------------------------------------------------------- shared bits
  AC.empty = (ic, title, text, actions = "") =>
    `<div class="empty"><div class="big">${icon(ic)}</div><h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}${actions ? `<div class="head-actions" style="justify-content:center;margin-top:8px">${actions}</div>` : ""}</div>`;
  AC.loading = () => `<div class="empty"><div class="big pulse">${icon("grid")}</div><h3>Loading</h3></div>`;
  AC.crumbs = (parts) =>
    `<nav class="crumbs" aria-label="Breadcrumb">${parts.map((p, i) => (p.href && i < parts.length - 1 ? `<a href="${p.href}">${esc(p.label)}</a>` : `<span>${esc(p.label)}</span>`)).join('<span class="sep">/</span>')}</nav>`;
  AC.showErr = (id, msg) => {
    const el = document.getElementById(id);
    if (el) el.textContent = msg;
    const inp = document.getElementById(id.replace(/^e-/, "f-"));
    if (inp) { inp.classList.toggle("bad", !!msg); if (msg) inp.focus(); }
  };

  // Reference-entity option labels for ref fields: name field first, then first text field.
  AC.refLabel = (row) => {
    if (!row) return "";
    if (row.name) return row.name;
    const ent = null;
    void ent;
    for (const k of Object.keys(row)) {
      if (typeof row[k] === "string" && row[k] && !["id", "parent_id", "owner_id"].includes(k)) return row[k];
    }
    return row.id;
  };
  AC.refOptions = (entityName) => {
    const cache = AC.entityCache(entityName);
    return (cache.rows || []).map((r) => ({ id: r.id, label: AC.refLabel(r) }));
  };

  // Columns for a browse table: the name-ish field first, then up to 3 other scalars.
  AC.browseFields = (entity) => {
    const fields = (entity && entity.fields) || [];
    const nameF = fields.find((f) => f.name === "name") || fields.find((f) => f.type === "text") || fields[0];
    const rest = fields.filter((f) => f !== nameF && !f.type.startsWith("list<ref>") && f.type !== "ref").slice(0, 3);
    return { nameF, rest };
  };
  AC.fieldText = (entity, row, field) => {
    if (!field) return `<span class="dim">—</span>`;
    if (field.type === "ref" || field.type === "list<ref>") {
      const resolve = (id) => {
        const target = field.to && AC.entityCache(field.to);
        const found = target && target.byId[id];
        return found ? AC.refLabel(found) : id;
      };
      return AC.fieldCell(field, row[field.name], resolve);
    }
    return AC.fieldCell(field, row[field.name]);
  };

  // ---------------------------------------------------------------- transition buttons (shared with admin queue)
  // One button per allowed edge; edges that collect/require data open a sheet first.
  AC.edgeButtons = (entityName, record, lineOrNull) => {
    const state = lineOrNull ? AC.lineState(lineOrNull) : AC.recordState(record);
    const edges = AC.allowedEdges(state, AC.recordOwnerId(record));
    if (!edges.length) return `<span class="dim">—</span>`;
    return `<span class="row-actions" style="display:flex;gap:6px;flex-wrap:wrap">` + edges.map((e) => {
      const needs = e.requires || e.collects;
      const label = e.to.replace(/_/g, " ");
      const cls = /approv|allow|yes|grant/.test(e.to) ? "btn btn-fill sm" : /den|reject|no|revoke/.test(e.to) ? "btn btn-danger sm" : "btn btn-tonal sm";
      return `<button class="${cls}" data-act="tr-go" data-ent="${esc(entityName)}" data-rid="${esc(record.id)}"` +
        (lineOrNull ? ` data-line="${esc(lineOrNull.id)}"` : "") +
        ` data-to="${esc(e.to)}"${needs ? ` data-need="${esc(needs)}"` : ""}>${esc(label)}</button>`;
    }).join("") + `</span>`;
  };
  AC.act["tr-go"] = async (t) => {
    const { ent, rid, line, to, need } = t.dataset;
    const doIt = async (data) => {
      const updated = await AC.transition(ent, rid, to, { lineId: line || null, data });
      AC.replaceRecord(ent, updated);
      const inReview = AC.rv && AC.rv.id === rid && !$("#sheet").hidden;
      if (!inReview) AC.sheet.close();
      AC.render();
      if (inReview && AC.refreshSheet) AC.refreshSheet();
      AC.toast(`Saved: ${to.replace(/_/g, " ")}.`);
    };
    if (!need) {
      t.disabled = true;
      try { await doIt(null); } finally { t.disabled = false; }
      return;
    }
    AC.sheet.open(AC.sheetHtml({
      title: `“${to.replace(/_/g, " ")}” — add ${need}`,
      body: `<div class="field"><label for="tr-text">${esc(need)}</label>` +
        `<textarea class="txt" id="tr-text" rows="3" maxlength="2000" placeholder="Write it here…"></textarea><div class="err" id="e-form"></div></div>`,
      footer: `<button class="btn btn-text" data-act="sheet-close">Cancel</button><button class="btn btn-fill" id="tr-save">Save</button>`,
    }));
    document.getElementById("tr-save").addEventListener("click", async (ev) => {
      const v = (document.getElementById("tr-text").value || "").trim();
      if (!v) { AC.showErr("e-form", "Write it first."); return; }
      ev.target.disabled = true;
      try { await doIt({ [need]: v }); } catch (e) { AC.showErr("e-form", e.message); ev.target.disabled = false; }
    });
  };
  // Swap a refreshed record into every cache that holds it, then re-render.
  AC.replaceRecord = (entityName, updated) => {
    for (const list of [S.mine, S.queue, S.reportRows]) {
      const i = (list || []).findIndex((r) => r.id === updated.id);
      if (i >= 0) list[i] = updated;
    }
    const cache = AC.entityCache(entityName);
    if (cache.byId[updated.id]) {
      cache.byId[updated.id] = updated;
      const i = cache.rows.findIndex((r) => r.id === updated.id);
      if (i >= 0) cache.rows[i] = updated;
    }
  };

  // ---------------------------------------------------------------- browse_pick (catalog)
  const browseOf = () => AC.viewOf("browse_pick");
  const submitEntityOf = () => {
    const b = browseOf();
    return (b && b.submit && AC.entityOf(b.submit)) || null;
  };

  const matches = (row, node, fields, q) =>
    [row.name, AC.refLabel(row), node ? AC.pathOf(node).join(" ") : "", fields.map((f) => String(row[f.name] ?? "")).join(" ")]
      .join(" ").toLowerCase().includes(q);

  AC.views.catalog = () => {
    if (!S.me) return AC.authScreen();
    const browse = browseOf();
    if (!AC.spec) {
      // Fresh install: no spec yet. Whoever may design lands on the prompt-first screen.
      if (AC.canDesign()) return AC.views.design();
      const body = AC.schemaEdit ? "Nothing has been set up yet. Sign in as an admin to design the app."
        : "Nothing has been set up yet. Set SCHEMA_EDIT=1 to use the designer, or start with EXAMPLE=access_requests on a new database.";
      return `<div class="page">${AC.empty("folder", "No app yet", body)}</div>`;
    }
    if (!browse) return `<div class="page">${AC.empty("folder", "No browse view", "The active spec has no browse_pick view.")}</div>`;
    if (!S.loaded.catalog) return `<div class="page">${AC.loading()}</div>`;
    const entity = AC.entityOf(browse.entity);
    const treeName = browse.tree && browse.tree !== browse.entity ? browse.tree : null;
    const itemCache = AC.entityCache(browse.entity);
    const treeCache = itemCache; // the item cache carries the topic tree with items attached
    const node = S.route.id ? treeCache.nodes[S.route.id] : null;
    if (S.route.id && !node) {
      return `<div class="page">${AC.empty("folder", "This topic no longer exists", "It may have been renamed or removed by an admin.", `<a class="btn btn-tonal" href="#/catalog">Browse all items</a>`)}</div>`;
    }
    const { nameF, rest } = AC.browseFields(entity);
    const q = S.q.trim().toLowerCase();
    const selectedTab = S.catTab === "selected";
    let list = selectedTab ? [...S.sel].map((id) => itemCache.byId[id]).filter(Boolean)
      : node ? AC.subItems(node) : treeName ? [...(itemCache.rows || [])] : (treeCache.roots || []).flatMap(AC.subItems);
    const total = list.length;
    if (q) list = list.filter((it) => matches(it, it.node, [nameF, ...rest].filter(Boolean), q));

    const crumbs = [{ label: (browse.label || "Browse"), href: "#/catalog" }];
    if (node) {
      const chain = [];
      for (let n = node; n; n = n.parent) chain.unshift(n);
      chain.forEach((n) => crumbs.push({ label: n.name, href: `#/catalog/${n.id}` }));
    }

    const rows = list.map((it) => {
      const on = S.sel.has(it.id);
      return `<tr class="${on ? "on" : ""}" data-act="toggle-item" data-id="${it.id}">` +
        `<td class="c-chk"><input type="checkbox" ${on ? "checked" : ""} aria-label="Select ${esc(AC.refLabel(it))}"></td>` +
        `<td><div class="nm">${esc(AC.refLabel(it))}</div>${it.node ? `<div class="path">${esc(AC.pathOf(it.node).join(" / "))}</div>` : ""}</td>` +
        rest.map((f) => `<td class="hide-sm">${AC.fieldText(entity, it, f)}</td>`).join("") +
        `</tr>`;
    }).join("");

    let body;
    if (!list.length) {
      body = selectedTab
        ? AC.empty("check-circle", "Nothing selected yet", "Tick the items you need on the Items tab, then submit them all at once.", `<button class="btn btn-tonal" data-act="cat-tab" data-tab="items">Browse items</button>`)
        : q
          ? AC.empty("search", "No items match your filter", `Nothing matches "${S.q.trim()}". Try a shorter word or clear the filter.`, `<button class="btn btn-tonal" data-act="clear-filter">Clear filter</button>`)
          : AC.empty("folder", "Nothing here yet", "An admin can add items from the reference editor.");
    } else {
      body = `<div class="tbl-wrap"><table class="tbl"><thead><tr>` +
        `<th class="c-chk"><input type="checkbox" data-act="toggle-all" aria-label="Select all shown items"></th>` +
        `<th>Name</th>${rest.map((f) => `<th class="hide-sm">${esc(f.label || f.name)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    const title = node ? node.name : (browse.label || "Browse");
    const sub = node ? `${node._count} item${node._count === 1 ? "" : "s"} in this topic and the topics inside it.`
      : `${total} items. Tick what you need, then submit once.`;

    return `<div class="page">${AC.crumbs(crumbs)}
      <div class="pagehead"><div><h1 class="h1">${esc(title)}</h1><p class="sub">${esc(sub)}</p></div>
        <div class="head-actions"><button class="btn btn-fill" data-act="open-submit" ${S.sel.size || !submitEntityOf()?.lines ? "" : "disabled"}>${icon("plus")}Submit${S.sel.size ? ` (${S.sel.size})` : ""}</button></div></div>
      <div class="tabs" role="tablist">
        <button class="tab${selectedTab ? "" : " on"}" role="tab" aria-selected="${!selectedTab}" data-act="cat-tab" data-tab="items">Items</button>
        <button class="tab${selectedTab ? " on" : ""}" role="tab" aria-selected="${selectedTab}" data-act="cat-tab" data-tab="selected">Selected<span class="tc">${S.sel.size}</span></button>
      </div>
      <div class="filterbar"><label class="filter" for="flt">${icon("filter")}<input id="flt" type="text" autocomplete="off" placeholder="Filter items" value="${esc(S.q)}" aria-label="Filter items">${S.q ? `<button class="fx" type="button" data-act="clear-filter" aria-label="Clear filter">${icon("x")}</button>` : ""}</label>
        <span class="count">${q ? `${list.length} of ${total}` : `${total} item${total === 1 ? "" : "s"}`}</span></div>
      ${body}
      ${S.sel.size ? `<div class="actionbar"><span class="n">${S.sel.size} selected</span><button class="btn btn-text" data-act="clear-sel">Clear</button><span class="spacer"></span><button class="btn btn-fill" data-act="open-submit">${icon("plus")}Submit</button></div>` : ""}
    </div>`;
  };

  AC.inputs.flt = (el) => { S.q = el.value; const gs = AC.$("#gs"); if (gs) gs.value = S.q; AC.renderPage(); };
  AC.act["clear-filter"] = () => { S.q = ""; const gs = AC.$("#gs"); if (gs) gs.value = ""; AC.renderPage(); };
  AC.act["cat-tab"] = (t) => { S.catTab = t.dataset.tab; AC.renderPage(); };
  AC.act["toggle-item"] = (t) => {
    if (!S.me) return AC.act["open-auth"]({ dataset: { mode: "login" } });
    const id = t.dataset.id;
    S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id);
    AC.saveSel();
    AC.renderPage();
  };
  AC.act["toggle-all"] = (t) => {
    if (!S.me) return AC.act["open-auth"]({ dataset: { mode: "login" } });
    const browse = browseOf();
    const itemCache = AC.entityCache(browse.entity);
    const ids = [...document.querySelectorAll("tbody tr[data-id]")].map((tr) => tr.dataset.id).filter((id) => itemCache.byId[id]);
    const allOn = ids.length > 0 && ids.every((id) => S.sel.has(id));
    ids.forEach((id) => (allOn ? S.sel.delete(id) : S.sel.add(id)));
    AC.saveSel();
    AC.renderPage();
  };
  AC.act["clear-sel"] = () => { S.sel.clear(); AC.saveSel(); AC.renderPage(); };

  // ---------------------------------------------------------------- submit sheet (generic over the submit entity)
  AC.act["open-submit"] = () => {
    const submit = submitEntityOf();
    if (!submit) return;
    if (!S.sel.size && submit.lines) return;
    if (!S.me) {
      AC.sheet.open(AC.sheetHtml({
        title: "Sign in to continue",
        body: `<p class="sub">Sign in or create an account to submit — your selection stays.</p>`,
        footer: `<button class="btn btn-text" data-act="sheet-close">Later</button><span class="spacer"></span><button class="btn btn-tonal" data-act="open-auth" data-mode="signup">Create account</button><button class="btn btn-fill" data-act="open-auth" data-mode="login">Sign in</button>`,
      }));
      return;
    }
    const scalar = (submit.fields || []).filter((f) => !(submit.lines && f.name === submit.lines.entity_ref));
    // Prefill a ref field that points at the browsed entity from the current selection.
    const values = {};
    const browse = browseOf();
    if (!submit.lines && browse) {
      const refF = scalar.find((f) => f.type === "ref" && f.to === browse.entity);
      if (refF && S.sel.size === 1) values[refF.name] = [...S.sel][0];
    }
    const picked = submit.lines
      ? `<div class="lbl" style="margin:12px 0 6px">Selected (${S.sel.size})</div><div class="chips">${[...S.sel].map((id) => {
          const it = AC.entityCache(browse.entity).byId[id];
          return it ? `<span class="chip">${esc(AC.refLabel(it))}<button type="button" data-act="unselect" data-id="${it.id}" aria-label="Remove ${esc(AC.refLabel(it))}">${icon("x")}</button></span>` : "";
        }).join("")}</div>` : "";
    AC.sheet.open(AC.sheetHtml({
      title: submit.label || "Submit",
      body: `<div class="rv-who"><span class="avatar">${esc(AC.initials(S.me.name))}</span><div><div class="nm">${esc(S.me.name)}</div><div class="path">${esc(S.me.email)}</div></div></div>` +
        picked + AC.recordForm(submit, values, (name) => AC.refOptions(name)) + `<div class="err" id="e-form"></div>`,
      footer: `<button class="btn btn-text" data-act="sheet-close">Cancel</button><button class="btn btn-fill" id="btn-submit" data-act="submit-record">Submit</button>`,
    }));
  };
  AC.act.unselect = (t) => {
    S.sel.delete(t.dataset.id);
    AC.saveSel();
    AC.renderPage();
    if (S.sel.size) AC.act["open-submit"]();
    else AC.sheet.close();
  };
  AC.act["submit-record"] = async (btn) => {
    const submit = submitEntityOf();
    if (!submit) return;
    AC.showErr("e-form", "");
    const data = AC.readForm(submit);
    if (submit.lines) {
      if (!S.sel.size) return AC.showErr("e-form", "Select at least one item.");
      data.lines = [...S.sel];
    }
    btn.disabled = true;
    try {
      await AC.api(`/api/e/${encodeURIComponent(submit.name)}`, { method: "POST", body: data });
      const n = S.sel.size;
      S.sel.clear();
      AC.saveSel();
      AC.sheet.close();
      await AC.loadMine();
      AC.toast(submit.lines ? `Submitted with ${n} item(s). Watch My records for the decision.` : "Submitted. Watch My records for the decision.");
      if (S.route.name === "mine") AC.render(); else location.hash = "#/mine";
    } catch (e) {
      AC.showErr("e-form", e.message);
    } finally {
      btn.disabled = false;
    }
  };

  // ---------------------------------------------------------------- my_records: generic record cards
  S.commentDraft = S.commentDraft || {};
  document.addEventListener("input", (e) => {
    const id = e.target && e.target.id;
    if (id && id.startsWith("c-")) S.commentDraft[id.slice(2)] = e.target.value;
  });

  const recordFields = (entity, rec) =>
    (entity.fields || []).filter((f) => !["ref", "list<ref>"].includes(f.type) || rec[f.name]).map((f) =>
      `<div class="kv"><span>${esc(f.label || f.name)}</span><span>${AC.fieldText(entity, rec, f)}</span></div>`).join("");

  const lineRows = (entityName, rec) => (rec.lines || []).map((line) => {
    const ref = line.ref || {};
    const needs = AC.allowedEdges(AC.lineState(line), AC.recordOwnerId(rec)).some((e) => e.requires);
    const just = line.justification || line[Object.keys(line).find((k) => /justif/.test(k)) || ""];
    return `<tr style="cursor:default"><td><div class="nm">${esc(AC.refLabel(ref))}</div></td>` +
      `<td>${AC.stPill(AC.lineState(line))}${line.state === "needs_info" || /needs_info/.test(AC.lineState(line)) ? ` <span class="st st-warn">Needs your reply</span>` : ""}</td>` +
      `<td>${just ? `<div class="quote">${esc(just)}</div>` : `<span class="dim">—</span>`}</td>` +
      `<td>${AC.edgeButtons(entityName, rec, line)}</td></tr>`;
  }).join("");

  const activityHtml = (entityName, rec) => {
    const ev = [`<div class="act-row"><span class="dot"></span><div><b>Submitted</b><span class="dim"> · ${esc(AC.fmtDate(rec.created_at))}</span></div></div>`];
    const list = S.comments[rec.id];
    if (list && list.length) {
      list.forEach((c) => ev.push(`<div class="act-row"><span class="avatar sm">${esc(AC.initials(c.author_name))}</span><div><b>${esc(c.author_name)}</b>${c.is_admin ? ` <span class="st st-partial">Admin</span>` : ""}<span class="dim"> · ${esc(AC.ago(c.created_at))}</span><div class="quote" style="margin-top:4px">${esc(c.body)}</div></div></div>`));
    } else if (list && !list.length) {
      ev.push(`<div class="act-row"><span class="dim">No messages yet.</span></div>`);
    }
    return ev.join("");
  };

  AC.act["mine-toggle"] = async (t) => {
    const id = t.dataset.id;
    if (S.mineOpen.has(id)) { S.mineOpen.delete(id); AC.renderPage(); return; }
    S.mineOpen.add(id);
    AC.renderPage();
    try {
      const ent = AC.myEntity();
      if (ent) { await AC.loadComments(ent, id, true); AC.renderPage(); }
    } catch { /* list still usable */ }
  };
  AC.act["mine-all"] = async (t) => {
    const expand = t.dataset.expand === "1";
    S.mineOpen = new Set(expand ? S.mine.map((r) => r.id) : []);
    AC.renderPage();
    if (expand) {
      try {
        const ent = AC.myEntity();
        if (ent) await Promise.all(S.mine.map((r) => AC.loadComments(ent, r.id, true)));
        AC.renderPage();
      } catch { /* ignore */ }
    }
  };
  AC.act["mine-send"] = async (t) => {
    const id = t.dataset.id;
    const box = document.getElementById(`c-${id}`);
    const body = (box?.value || S.commentDraft[id] || "").trim();
    if (!body) { AC.toast("Write your message first.", "err"); return; }
    t.disabled = true;
    try {
      const ent = AC.myEntity();
      await AC.postComment(ent, id, body);
      S.commentDraft[id] = "";
      await AC.loadMine();
      AC.render();
      AC.toast("Sent.");
    } catch (err) { AC.fail(err); } finally { t.disabled = false; }
  };

  AC.views.mine = () => {
    if (!S.me) return AC.authScreen();
    const view = AC.viewOf("my_records");
    const entity = view && AC.entityOf(view.entity);
    if (!entity) return `<div class="page">${AC.empty("inbox", "No records view", "The active spec has no my_records view.")}</div>`;
    if (!S.mine.length) {
      return `<div class="page">${AC.crumbs([{ label: "My records" }, { label: view.label || entity.label || entity.name }])}
        <div class="pagehead"><div><h1 class="h1">${esc(view.label || "My records")}</h1><p class="sub">Signed in as ${esc(S.me.name)} (${esc(S.me.email)}). Decisions appear here as soon as they are made.</p></div>
          <div class="head-actions"><a class="btn btn-tonal" href="#/catalog">${icon("plus")}New submission</a></div></div>
        ${AC.empty("inbox", "Nothing here yet", "Browse the catalog and submit — it shows up here.", `<a class="btn btn-fill" href="#/catalog">Browse</a>`)}</div>`;
    }
    const ordered = [...S.mine].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    if (!S.mineOpen.size && ordered.length) {
      S.mineOpen.add(ordered[0].id);
      const ent0 = view.entity;
      AC.loadComments(ent0, ordered[0].id, true).then(() => AC.renderPage()).catch(() => {});
    }
    const cards = ordered.map((r, idx) => {
      const open = S.mineOpen.has(r.id);
      const state = AC.recordState(r);
      const draft = S.commentDraft[r.id] || "";
      const lines = Array.isArray(r.lines) && r.lines.length
        ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Item</th><th>State</th><th>Note</th><th>Decide</th></tr></thead><tbody>${lineRows(view.entity, r)}</tbody></table></div>`
        : AC.edgeButtons(view.entity, r, null);
      return `<article class="req-card${open ? " open" : ""}">
        <button class="req-head" data-act="mine-toggle" data-id="${r.id}" aria-expanded="${open}" aria-label="${open ? "Collapse" : "Expand"} record from ${esc(AC.fmtDate(r.created_at))}">
          <span class="req-chev${open ? " open" : ""}">${icon("chevron-right")}</span>
          <span class="req-main"><span class="req-title">#${ordered.length - idx} · ${esc(AC.fmtDate(r.created_at))} <span class="dim">· ${esc(AC.ago(r.created_at))}</span></span>
            <span class="req-sub">${esc(String((entity.fields[0] && r[entity.fields[0].name]) ?? "").slice(0, 120))}</span></span>
          <span class="req-meta">${AC.stPill(state)}</span>
        </button>
        ${open ? `<div class="req-body"><div class="kv">${recordFields(entity, r)}</div>${lines}
          <h4 class="h2" style="margin-top:14px">Discussion${(S.comments[r.id] || []).length ? ` (${(S.comments[r.id] || []).length})` : ""}</h4>
          <div class="activity">${activityHtml(view.entity, r)}</div>
          <div class="comment-box"><textarea class="txt" id="c-${r.id}" rows="2" maxlength="2000" placeholder="Reply with more context…">${esc(draft)}</textarea>
            <div style="display:flex;justify-content:flex-end;margin-top:8px"><button class="btn btn-fill" data-act="mine-send" data-id="${r.id}">Send</button></div></div>
        </div>` : ""}</article>`;
    }).join("");
    const allOpen = ordered.length > 0 && S.mineOpen.size === ordered.length;
    return `<div class="page">${AC.crumbs([{ label: "My records" }, { label: view.label || entity.label || entity.name }])}
      <div class="pagehead"><div><h1 class="h1">${esc(view.label || "My records")}</h1><p class="sub">Signed in as ${esc(S.me.name)} (${esc(S.me.email)}). Open a record — every line shows its state.</p></div>
        <div class="head-actions"><button class="btn btn-outline" data-act="mine-all" data-expand="${allOpen ? "0" : "1"}">${allOpen ? "Collapse all" : "Expand all"}</button><a class="btn btn-tonal" href="#/catalog">${icon("plus")}New submission</a></div></div>
      <div class="req-list">${cards}</div></div>`;
  };
})();
