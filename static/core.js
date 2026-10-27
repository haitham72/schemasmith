/* schemasmith: shared core. Token auth, spec-driven nav/routes, sheet, toast, theme, poll. */
(() => {
  const AC = (window.AC = { views: {}, act: {}, inputs: {}, changes: {}, dnd: {}, fields: (window.AC && window.AC.fields) || {} });
  const S = (AC.S = {
    token: "", me: null, pendingEmail: "",
    authMode: "login", authErr: "",
    spec: null, meta: { title: "schemasmith" },
    // Generic entity caches keyed by entity name: {rows, byId, roots, nodes}.
    store: {},
    q: "", sel: new Set(), open: new Set(),
    mine: [], mineOpen: new Set(),
    queue: [], exports: [], dashboard: null,
    users: [], allowed: [], userTab: "pending", userQ: "",
    route: { name: "catalog", id: null },
    reqTab: "pending", reqQ: "", armApprove: false,
    navClosed: {}, cmSel: null,
    loaded: {},
  });
  AC.meta = S.meta;

  const $ = (AC.$ = (sel, root = document) => root.querySelector(sel));
  AC.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const esc = AC.esc;
  AC.icon = (name, cls = "") => `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${esc(name)}"/></svg>`;
  AC.ICONS = ["folder", "code", "terminal", "cpu", "zap", "layers", "globe", "film", "mic", "sun", "map", "search", "credit-card", "message-circle", "trending-up", "type", "share-2", "cloud", "package", "git-branch", "database", "server", "shield", "link"];
  AC.iconOr = (name) => (AC.ICONS.includes(name) ? name : "folder");

  AC.store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage may be blocked */ } },
  };

  AC.initials = (name) => (String(name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase();
  AC.fmtDate = (iso) => { try { return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }); } catch { return ""; } };
  AC.ago = (iso) => {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return Math.floor(s / 86400) + " d ago";
  };
  AC.stPill = (st) => `<span class="st st-${esc(st)}">${esc(AC.stateLabel(st))}</span>`;
  AC.stateLabel = (st) => {
    const wf = AC.spec && AC.spec.workflow;
    const labels = { pending: "Pending", approved: "Approved", denied: "Denied", partial: "Partly decided" };
    if (wf && (wf.states || []).includes(st)) {
      return labels[st] || st.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    }
    return labels[st] || st;
  };
  AC.userStPill = (st) => {
    const map = { pending: "st-pending", approved: "st-ok", rejected: "st-denied" };
    const label = { pending: "Waiting approval", approved: "Approved", rejected: "Not approved" }[st] || st;
    return `<span class="st ${map[st] || "st-pending"}">${esc(label)}</span>`;
  };

  // ---------------------------------------------------------------- API (Bearer [REDACTED]
  AC.api = async (path, { method = "GET", body, headers: extra } = {}) => {
    const headers = { ...(extra || {}) };
    if (S.token) headers["Authorization"] = "Bearer " + S.token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const r = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
    if (!r.ok) {
      let msg = r.statusText || "Request failed";
      try {
        const j = await r.json();
        msg = typeof j.detail === "string" ? j.detail : (j.detail && typeof j.detail.message === "string") ? j.detail.message : Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join("; ") : msg;
      } catch { /* not JSON */ }
      const e = new Error(msg);
      e.status = r.status;
      throw e;
    }
    const ct = r.headers.get("content-type") || "";
    return ct.includes("json") ? r.json() : r.text();
  };
  AC.fail = (e) => AC.toast(e && e.message ? e.message : "Something went wrong.", "err");

  // ---------------------------------------------------------------- auth
  AC.saveToken = (t) => { S.token = t || ""; AC.store.set("ac.token", S.token); };
  AC.refreshMe = async () => {
    S.me = null;
    if (S.token) {
      try {
        S.me = await AC.api("/api/me");
      } catch (e) {
        if (e && e.status === 401) AC.saveToken("");
        S.me = null;
      }
    }
    if (S.me) await Promise.all([AC.loadMine().catch(() => {}), AC.loadQueue().catch(() => {})]);
    else { S.mine = []; S.queue = []; S.users = []; S.allowed = []; }
  };
  AC.setSession = async (token) => {
    AC.saveToken(token || "");
    await AC.refreshMe();
    AC.render();
  };
  AC.logout = async () => {
    try { if (S.token) await AC.api("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    await AC.setSession("");
    AC.toast("Signed out.");
    if (S.route.name.startsWith("admin") || S.route.name === "mine") location.hash = "#/catalog";
  };

  const authSheet = (mode) => {
    S.authMode = mode;
    const login = mode === "login";
    AC.sheet.open(AC.sheetHtml({
      title: login ? "Sign in" : "Create account",
      body: `<p class="sub" style="margin:0 0 16px">${login
        ? "Welcome back. Use the email and password you signed up with."
        : "One account per person. An admin approves new accounts — or pre-approves your email so you get in immediately."}</p>
        <div class="err" id="e-form"></div>
        ${login ? "" : `<div class="field"><label for="a-name">Full name</label>
          <input class="inp" id="a-name" autocomplete="name" placeholder="Jane Doe" value="${esc(AC._auth?.name || "")}"></div>`}
        <div class="field"><label for="a-email">Email</label>
          <input class="inp" id="a-email" type="email" autocomplete="email" placeholder="name@example.com" value="${esc(AC._auth?.email || "")}"></div>
        <div class="field"><label for="a-pass">${login ? "Password" : "Password (at least 6 characters)"}</label>
          <input class="inp" id="a-pass" type="password" autocomplete="${login ? "current-password" : "new-password"}" placeholder="••••••••"></div>
        <div class="help">${login ? "" : "After you sign up you will see one of two things: immediate access, or “waiting for approval”. Ask an admin if you wait more than a day."}</div>`,
      footer: `<button class="btn btn-text" data-act="auth-switch">${login ? "Need an account? Sign up" : "Have an account? Sign in"}</button><span class="spacer"></span><button class="btn btn-text" data-act="sheet-close">Cancel</button><button class="btn btn-fill" data-act="auth-go">${login ? "Sign in" : "Create account"}</button>`,
    }));
  };
  AC._auth = { name: "", email: "" };
  AC.act["open-auth"] = (t) => { AC._auth = { name: "", email: "" }; authSheet(t.dataset.mode || "login"); };
  AC.act["auth-switch"] = () => {
    const g = (id) => document.getElementById(id);
    if (g("a-name")) AC._auth.name = g("a-name").value;
    if (g("a-email")) AC._auth.email = g("a-email").value;
    authSheet(S.authMode === "login" ? "signup" : "login");
  };
  AC.act["auth-go"] = async (btn) => {
    const g = (id) => document.getElementById(id);
    const errBox = document.getElementById("e-form");
    const show = (m) => { if (errBox) errBox.textContent = m; };
    show("");
    const email = (g("a-email")?.value || "").trim();
    const pass = g("a-pass")?.value || "";
    const login = S.authMode === "login";
    const name = login ? "x" : (g("a-name")?.value || "").trim();
    if (!login && name.length < 2) return show("Enter your full name.");
    if (!/.+@.+\..+/.test(email)) return show("Enter a valid email address.");
    if (!login && pass.length < 6) return show("Password needs at least 6 characters.");
    if (!pass) return show("Enter your password.");
    btn.disabled = true;
    try {
      await AC.submitAuth(login ? "login" : "signup", name, email, pass);
    } catch (e) { show(e.message); } finally { btn.disabled = false; }
  };
  // Shared by the sheet and the full-page onboarding screen.
  AC.submitAuth = async (mode, name, email, pass) => {
    const url = mode === "login" ? "/api/auth/login" : "/api/auth/signup";
    const payload = mode === "login" ? { email, password: pass } : { name, email, password: pass };
    const res = await AC.api(url, { method: "POST", body: payload });
    if (res.token) {
      AC._auth = { name: "", email: "" };
      S.authErr = "";
      S.pendingEmail = "";
      AC.sheet.close();
      await AC.setSession(res.token);
      AC.toast(mode === "login" ? `Welcome back, ${res.name}.` : `Welcome, ${res.name}.`);
      if (mode !== "login" && res.is_admin) AC.toast("You are the first account, so you are admin. Pre-approve emails under Admin → Users.", "err");
      if (S.route.name === "mine" || S.route.name === "catalog") AC.render();
    } else {
      // Signed up but waiting for approval — no token.
      // Drop any previous session first, otherwise the old account stays logged in.
      AC.saveToken("");
      await AC.refreshMe();
      S.pendingEmail = email;
      S.authErr = "";
      AC.sheet.close();
      AC.render();
      AC.toast("Account created. Waiting for admin approval.");
    }
  };
  AC.act["account"] = () => {
    if (!S.me) { AC._auth = { name: "", email: "" }; return authSheet("login"); }
    AC.sheet.open(AC.sheetHtml({
      title: "Your account",
      body: `<div class="rv-who"><span class="avatar lg">${esc(AC.initials(S.me.name))}</span><div><div class="nm">${esc(S.me.name)}</div><div class="path">${esc(S.me.email)}${S.me.is_admin ? " · admin" : ""}${S.me.is_guest ? " · guest" : ""}</div></div></div>
        <div class="kv" style="margin-top:12px"><span>Status:</span><span>${AC.userStPill(S.me.status)}</span></div>
        <p class="sub" style="margin-top:12px">Signed in on this browser only. Signing out forgets the session here.</p>`,
      footer: `<button class="btn btn-danger" data-act="logout">Sign out</button><span class="spacer"></span><button class="btn btn-fill" data-act="sheet-close">Done</button>`,
    }));
  };
  AC.act.logout = () => { AC.sheet.close(); AC.logout(); };

  AC.act["pending-clear"] = () => { S.pendingEmail = ""; S.authMode = "login"; S.authErr = ""; AC.renderPage(); };

  // ---------------------------------------------------------------- onboarding screen (logged-out landing)
  // Empty screen: logo, name/email/password, sign in/up tabs, pending state,
  // and a forgot-password placeholder with no logic behind it yet.
  AC.authScreen = () => {
    const card = (inner) =>
      `<div class="page auth-page"><div class="auth-wrap"><div class="auth-card"><span class="logo auth-logo">${AC.icon("shield")}</span>${inner}</div></div></div>`;
    if (S.pendingEmail) {      return card(`<h1 class="h1">Waiting for approval</h1>
        <p class="sub">Account <b>${esc(S.pendingEmail)}</b> was created.<br>An admin needs to approve it before you can sign in.</p>
        <button class="btn btn-fill auth-big" data-act="pending-clear">Back to sign in</button>`);
    }
    if (S.authMode === "forgot") {
      return card(`<h1 class="h1">Reset password</h1>
        <p class="sub">Password reset isn't available yet — there's no logic behind this button for now. Ask your admin to help you get back in.</p>
        <button class="btn btn-tonal auth-big" data-act="page-auth-back">Back to sign in</button>`);
    }
    const signup = S.authMode === "signup";
    const dom = S.signupDomain || "";
    return card(`<h1 class="h1">${esc(S.meta.title || "schemasmith")}</h1>
      <p class="sub">One account to request access and track every decision.</p>
      <div class="auth-tabs" role="tablist">
        <button class="tab${signup ? "" : " on"}" role="tab" aria-selected="${!signup}" data-act="page-auth-mode" data-mode="login">Sign in</button>
        <button class="tab${signup ? " on" : ""}" role="tab" aria-selected="${signup}" data-act="page-auth-mode" data-mode="signup">Create account</button>
      </div>
      ${signup ? `<div class="field"><label for="p-name">Full name</label><input class="inp" id="p-name" autocomplete="name" placeholder="Jane Doe" value="${esc(AC._auth?.name || "")}"></div>` : ""}
      <div class="field"><label for="p-email">Email${signup && dom ? ` — @${esc(dom)} only` : ""}</label><input class="inp" id="p-email" type="email" autocomplete="email" placeholder="${signup && dom ? `name@${esc(dom)}` : "name@example.com"}" value="${esc(AC._auth?.email || "")}"></div>
      <div class="field"><label for="p-pass">Password${signup ? " (at least 6 characters)" : ""}</label><input class="inp" id="p-pass" type="password" autocomplete="${signup ? "new-password" : "current-password"}" placeholder="••••••••"></div>
      ${S.authErr ? `<div class="err" style="margin-bottom:12px">${esc(S.authErr)}</div>` : ""}
      <button class="btn btn-fill auth-big" data-act="page-auth-go">${signup ? "Create account" : "Sign in"}</button>
      <button class="btn btn-text" data-act="page-auth-forgot">Forgot password?</button>
      ${S.guestLogin ? `<div class="auth-or"><span>or</span></div>
      <button class="btn btn-tonal auth-big" data-act="guest-go">Continue as guest</button>
      <p class="help" style="text-align:center;margin-top:8px">Try the app without an account. Guests can browse and edit records but cannot manage users or the schema.</p>` : ""}`);
  };
  const readPageAuth = () => {
    const g = (id) => document.getElementById(id);
    if (g("p-name")) AC._auth.name = g("p-name").value;
    if (g("p-email")) AC._auth.email = g("p-email").value;
  };
  AC.act["page-auth-mode"] = (t) => { readPageAuth(); S.authMode = t.dataset.mode; S.authErr = ""; AC.renderPage(); };
  AC.act["page-auth-forgot"] = () => { readPageAuth(); S.authMode = "forgot"; S.authErr = ""; AC.renderPage(); };
  AC.act["guest-go"] = async (btn) => {
    if (btn) btn.disabled = true;
    try {
      const res = await AC.api("/api/auth/guest", { method: "POST" });
      S.authErr = "";
      S.pendingEmail = "";
      AC.sheet.close();
      await AC.setSession(res.token);
      AC.toast("Signed in as a guest.");
    } catch (e) { S.authErr = e.message; if (btn) btn.disabled = false; AC.renderPage(); }
  };
  AC.act["page-auth-back"] = () => { S.authMode = "login"; S.authErr = ""; AC.renderPage(); };
  AC.act["page-auth-go"] = async (btn) => {
    const g = (id) => document.getElementById(id);
    const signup = S.authMode === "signup";
    const name = ((g("p-name")?.value) || "").trim();
    const email = (g("p-email")?.value || "").trim();
    const pass = g("p-pass")?.value || "";
    AC._auth = { name, email };
    const fail = (m) => { S.authErr = m; AC.renderPage(); };
    if (signup && name.length < 2) return fail("Enter your full name.");
    if (!/.+@.+\..+/.test(email)) return fail("Enter a valid email address.");
    if (signup && S.signupDomain && !email.toLowerCase().endsWith("@" + (S.signupDomain || ""))) return fail(`Create the account with your @${S.signupDomain || ""} work email.`);
    if (signup && pass.length < 6) return fail("Password needs at least 6 characters.");
    if (!pass) return fail("Enter your password.");
    btn.disabled = true;
    try {
      await AC.submitAuth(signup ? "signup" : "login", name, email, pass);
    } catch (e) { S.authErr = e.message; AC.renderPage(); }
  };

  // ---------------------------------------------------------------- generic entity cache
  // Every list/detail read goes through /api/e/* and lands here keyed by entity name.
  AC.entityCache = (name) => (S.store[name] = S.store[name] || { rows: [], byId: {}, roots: [], nodes: {} });

  AC.loadEntity = async (name) => {
    const cache = AC.entityCache(name);
    const res = await AC.api(`/api/e/${encodeURIComponent(name)}`);
    const rows = (res && res.items) || [];
    cache.rows = rows;
    cache.byId = {};
    rows.forEach((r) => { cache.byId[r.id] = r; });
    AC.buildTree(name);
    return rows;
  };

  // Generic tree: rows of a hierarchy entity are roots/children via parent_id;
  // a grouped reference entity (parent = another entity) hangs under those nodes.
  AC.buildTree = (name) => {
    const cache = AC.entityCache(name);
    const ent = AC.entityOf(name);
    const nodes = {}, roots = [];
    const groupParent = ent && ent.parent;
    const selfTree = ent && ent.hierarchy && !groupParent;
    if (!selfTree && !groupParent) { cache.roots = []; cache.nodes = {}; return; }
    const pool = groupParent ? (AC.entityCache(groupParent).rows || []) : (cache.rows || []);
    pool.forEach((c) => { nodes[c.id] = { ...c, children: [], items: [], parent: null }; });
    pool.forEach((c) => {
      const n = nodes[c.id];
      if (c.parent_id && nodes[c.parent_id]) {
        n.parent = nodes[c.parent_id];
        nodes[c.parent_id].children.push(n);
      } else {
        roots.push(n);
      }
    });
    if (groupParent) {
      (cache.rows || []).forEach((item) => {
        const n = nodes[item.parent_id];
        if (!n) return;
        const it = { ...item, node: n };
        n.items.push(it);
        cache.byId[item.id] = it;
      });
    }
    const count = (n) => (n._count = (n.items ? n.items.length : 0) + n.children.reduce((a, c) => a + count(c), 0));
    roots.forEach(count);
    cache.roots = roots;
    cache.nodes = nodes;
  };

  AC.pathOf = (node) => { const p = []; for (let n = node; n; n = n.parent) p.push(n.name); return p; };
  AC.subItems = (node) => (node.items || []).concat(...(node.children || []).map(AC.subItems));
  AC.isDesc = (node, maybeAncestor) => { for (let n = node; n; n = n.parent) if (n === maybeAncestor) return true; return false; };

  // Load the entities the current spec views need: browse tree + submit + queue/report entity.
  AC.loadBrowseData = async () => {
    const browse = AC.viewOf("browse_pick");
    if (!browse) return;
    const treeName = browse.tree;
    const entityName = browse.entity;
    if (treeName && treeName !== entityName) {
      await AC.loadEntity(treeName);
      await AC.loadEntity(entityName);
      AC.buildTree(entityName);
    } else {
      await AC.loadEntity(entityName);
    }
    for (const id of [...S.sel]) {
      const cache = AC.entityCache(entityName);
      if (!cache.byId[id]) S.sel.delete(id);
    }
    AC.saveSel();
    S.loaded.catalog = true;
  };

  AC.saveSel = () => AC.store.set("ac.sel", [...S.sel]);

  // ---------------------------------------------------------------- spec-driven record loads
  // Every list/detail read goes through /api/e/*; the server scopes owners itself.
  AC.myEntity = () => { const v = AC.viewOf("my_records"); return (v && v.entity) || null; };
  AC.queueEntity = () => { const v = AC.viewOf("queue"); return (v && v.entity) || null; };
  AC.reportEntity = () => { const v = AC.viewOf("report"); return (v && v.entity) || null; };

  AC.loadMine = async () => {
    const name = AC.myEntity();
    S.mine = [];
    if (S.me && name) {
      const res = await AC.api(`/api/e/${encodeURIComponent(name)}`);
      S.mine = (res && res.items) || [];
    }
  };
  AC.loadQueue = async () => {
    const name = AC.queueEntity();
    S.queue = [];
    if (S.me && name) {
      try {
        const res = await AC.api(`/api/e/${encodeURIComponent(name)}`);
        S.queue = (res && res.items) || [];
      } catch { S.queue = []; }
    }
    S.loaded.queue = true;
  };
  AC.loadReport = async () => {
    const name = AC.reportEntity();
    S.reportRows = [];
    if (S.me && name) {
      try {
        const res = await AC.api(`/api/e/${encodeURIComponent(name)}`);
        S.reportRows = (res && res.items) || [];
      } catch { S.reportRows = []; }
    }
  };
  AC.loadMeta = async () => {
    try {
      const m = await AC.api("/api/meta");
      if (m && m.signup_domain) S.signupDomain = m.signup_domain;
      S.guestLogin = !!(m && m.guest_login);
      if (m && m.title) {
        S.meta.title = m.title;
        document.title = m.title;
        const brand = document.querySelector(".brand-name");
        if (brand) brand.textContent = m.title;
      }
    } catch { /* offline default */ }
  };
  AC.loadUsers = async () => {
    if (!S.me || !S.me.is_admin) return;
    const [users, allowed] = await Promise.all([AC.api("/api/admin/users?status=all"), AC.api("/api/admin/allowed-emails")]);
    S.users = users;
    S.allowed = allowed;
    S.loaded.users = true;
  };

  // Justification thread per record. Cached so expanding is instant; refreshed on open.
  S.comments = {};
  AC.loadComments = async (entity, id, force = false) => {
    if (!S.me) return [];
    if (!force && S.comments[id]) return S.comments[id];
    const list = await AC.api(`/api/e/${encodeURIComponent(entity)}/${encodeURIComponent(id)}/comments`);
    S.comments[id] = list;
    return list;
  };
  AC.postComment = async (entity, id, body) => {
    const c = await AC.api(`/api/e/${encodeURIComponent(entity)}/${encodeURIComponent(id)}/comments`, { method: "POST", body: { body } });
    (S.comments[id] = S.comments[id] || []).push(c);
    return c;
  };

  // ---------------------------------------------------------------- workflow helpers (mirror app/workflow.py)
  AC.myRoles = () => {
    const mine = new Set(S.me ? (S.me.roles || []) : []);
    if (S.me && S.me.is_admin) mine.add("admin");
    if (S.me && S.me.is_reviewer) mine.add("reviewer");
    return mine;
  };
  AC.recordOwnerId = (rec) => (rec && rec.owner && rec.owner.id) || (rec && rec.owner_id) || null;
  AC.isLineLevel = () => !!(AC.spec && AC.spec.workflow && AC.spec.workflow.level === "line");
  AC.lineState = (line) => (line && (line.state || line.decision)) || "pending";
  AC.recordState = (rec) => {
    if (!rec) return "pending";
    if (rec.state) return rec.state;
    if (Array.isArray(rec.lines)) return AC.rollup(rec.lines.map(AC.lineState));
    return (AC.spec && AC.spec.workflow && AC.spec.workflow.initial) || "pending";
  };
  // Client mirror of workflow.rollup(): undecided-only stays at none_decided.
  AC.rollup = (states) => {
    const wf = AC.spec && AC.spec.workflow;
    if (!wf) return states[0] || "pending";
    const rule = wf.rollup;
    if (!rule) return states.length && new Set(states).size === 1 ? states[0] : wf.initial;
    if (!states.some((s) => s in rule.all)) return rule.none_decided;
    if (new Set(states).size === 1 && states[0] in rule.all) return rule.all[states[0]];
    return rule.mixed;
  };
  // Edges the current user may take from a state (graph + roles + ownership).
  AC.allowedEdges = (state, ownerId) => {
    const wf = AC.spec && AC.spec.workflow;
    if (!wf) return [];
    const mine = AC.myRoles();
    const ent = AC.entityOf(wf.entity);
    const ownerRole = ent && ent.owner;
    return (wf.transitions || []).filter((e) => (e.from || []).includes(state)).filter((edge) => {
      const allowed = new Set((edge.roles || []).filter((r) => mine.has(r)));
      if (ownerRole && allowed.has(ownerRole) && ownerId && S.me && String(ownerId) !== String(S.me.id)) allowed.delete(ownerRole);
      return allowed.size > 0;
    });
  };
  AC.transition = async (entity, recordId, toState, { lineId = null, data = null } = {}) => {
    const body = { to_state: toState };
    if (lineId) body.line_id = lineId;
    if (data) body.data = data;
    return AC.api(`/api/e/${encodeURIComponent(entity)}/${encodeURIComponent(recordId)}/transition`,
      { method: "POST", body });
  };

  // ---------------------------------------------------------------- toast + sheet
  let toastTimer;
  AC.toast = (msg, kind = "") => {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.className = "toast"), kind === "err" ? 7000 : 4000);
  };
  AC.sheet = {
    open(html, { wide = false } = {}) {
      const el = $("#sheet");
      el.className = "sheet" + (wide ? " wide" : "");
      el.innerHTML = html;
      el.hidden = false;
      $("#scrim").hidden = false;
      const f = el.querySelector("[autofocus]") || el.querySelector("input:not([type=checkbox]),textarea,select");
      if (f) f.focus();
    },
    close() {
      const el = $("#sheet");
      el.hidden = true;
      el.innerHTML = "";
      $("#scrim").hidden = true;
    },
  };
  AC.sheetHtml = ({ title, body, footer }) =>
    `<div class="sheet-h"><h2>${esc(title)}</h2><button class="iconbtn" type="button" data-act="sheet-close" aria-label="Close">${AC.icon("x")}</button></div>` +
    `<div class="sheet-b">${body}</div><div class="sheet-f">${footer}</div>`;

  AC.copy = async (text) => {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall back below */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* ignore */ }
    ta.remove();
    return ok;
  };
  AC.download = (filename, text, type = "text/csv") => {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  };
  // Binary downloads (xlsx) need the Bearer [REDACTED], so plain <a href> won't do.
  AC.downloadFile = async (path, filename) => {
    const headers = {};
    if (S.token) headers["Authorization"] = "Bearer " + S.token;
    const r = await fetch(path, { headers });
    if (!r.ok) {
      let msg = r.statusText || "Download failed";
      try {
        const j = await r.json();
        if (typeof j.detail === "string") msg = j.detail;
      } catch { /* not JSON */ }
      throw new Error(msg);
    }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  };

  // ---------------------------------------------------------------- nav (spec-driven)
  AC.navRoute = (name, id) => AC.routeHash(name, id);
  const link = (href, icon, label, { active, badge, alert } = {}) =>
    `<div class="nrow"><span class="nchev none"></span><a class="nlink${active ? " active" : ""}" href="${href}"${active ? ' aria-current="page"' : ""}>${AC.icon(icon)}<span class="nl">${esc(label)}</span>${badge != null ? `<span class="nbadge${alert ? " alert" : ""}">${badge}</span>` : ""}</a></div>`;

  const treeHtml = (nodes, depth, base) =>
    nodes.map((n) => {
      const kids = n.children.length > 0;
      const open = S.open.has(n.id);
      const active = S.route.name === "catalog" && S.route.id === n.id;
      return `<div class="nrow" style="padding-left:${depth * 14}px">` +
        (kids ? `<button class="nchev${open ? " open" : ""}" type="button" data-act="node-toggle" data-id="${n.id}" aria-label="${open ? "Collapse" : "Expand"} ${esc(n.name)}" aria-expanded="${open}">${AC.icon("chevron-right")}</button>` : `<span class="nchev none"></span>`) +
        `<a class="nlink${active ? " active" : ""}" href="${AC.navRoute("catalog", n.id)}"${active ? ' aria-current="page"' : ""}>${AC.icon(AC.iconOr(n.icon))}<span class="nl">${esc(n.name)}</span><span class="nbadge">${n._count}</span></a></div>` +
        (kids && open ? treeHtml(n.children, depth + 1, base) : "");
    }).join("");

  const section = (key, title, inner) =>
    `<div class="nsec${S.navClosed[key] ? " closed" : ""}"><button class="nsec-h" type="button" data-act="sec" data-sec="${key}" aria-expanded="${!S.navClosed[key]}">${esc(title)}${AC.icon("chevron-down")}</button><div class="nsec-b">${inner}</div></div>`;

  AC.renderNav = () => {
    const r = S.route;
    const views = (AC.spec && AC.spec.views) || [];
    const seen = new Set();
    let h = "";
    const browse = views.find((v) => v.type === "browse_pick");
    if (browse) {
      const cache = AC.entityCache(browse.entity);
      const tree = cache; // item cache carries the topic tree with items attached
      const count = browse.tree && browse.tree !== browse.entity
        ? (cache.rows || []).length
        : (tree.roots || []).reduce((a, n) => a + (n._count || 0), 0);
      h += section("browse", (browse.label || "Browse"),
        link(AC.navRoute("catalog"), "grid", "All items", { active: r.name === "catalog" && !r.id, badge: count }) +
        treeHtml(tree.roots || [], 0));
    }
    if (AC.canDesign && AC.canDesign()) {
      h = section("design", "Design", link(AC.navRoute("design"), "zap", "Describe your use case", { active: r.name === "design" })) + h;
    }
    const mine = views.find((v) => v.type === "my_records");
    if (mine && !seen.has("mine")) {
      seen.add("mine");
      h += section("mine", (mine.label || "My records"),
        link(AC.navRoute("mine"), "inbox", (mine.label || "My requests"), { active: r.name === "mine", badge: S.mine.length || null }));
    }
    if (S.me && (S.me.is_admin || S.me.is_reviewer)) {
      const adminViews = views.filter((v) => ["queue", "report", "exports", "dashboard", "reference_editor"].includes(v.type) && AC.hasRole(S.me, v.roles));
      if (adminViews.length) {
        const icons = { queue: "list", report: "check-circle", exports: "shield", dashboard: "layers", reference_editor: "folder" };
        const names = { queue: "admin-requests", report: "review", exports: "admin-allowlist", dashboard: "admin-overview", reference_editor: "admin-catalog" };
        h += section("admin", "Admin", adminViews.map((v) =>
          link(AC.navRoute(names[v.type]), icons[v.type] || "grid", v.label || v.type, { active: r.name === names[v.type] })).join(""));
      }
      if (S.me.is_admin) {
        h += section("users", "Users",
          link(AC.navRoute("admin-users"), "user", "Users", { active: r.name === "admin-users" }));
      }
    }
    $("#nav").innerHTML = h;
  };

  AC.renderWho = () => {
    const b = $("#who");
    b.innerHTML = S.me
      ? `<span class="wn">${esc(S.me.name)}</span><span class="avatar">${esc(AC.initials(S.me.name))}</span>`
      : `<span class="wn">Sign in</span><span class="avatar">${AC.icon("user")}</span>`;
    b.title = S.me ? `${S.me.name} · ${S.me.email}${S.me.is_admin ? " · admin" : ""}` : "Sign in or create an account";
  };

  // ---------------------------------------------------------------- render + routing
  AC.renderPage = () => {
    const page = $("#page");
    const scroll = page.scrollTop;
    const ae = document.activeElement;
    const keep = ae && ae.id && page.contains(ae) && /^(INPUT|TEXTAREA)$/.test(ae.tagName) ? { id: ae.id, s: ae.selectionStart, e: ae.selectionEnd } : null;
    const view = AC.views[S.route.name] || AC.views.catalog;
    page.innerHTML = view ? view() : "";
    page.scrollTop = scroll;
    if (keep) {
      const el = document.getElementById(keep.id);
      if (el) { el.focus(); try { el.setSelectionRange(keep.s, keep.e); } catch { /* not a text input */ } }
    }
  };
  AC.render = () => { AC.renderWho(); AC.renderNav(); AC.renderPage(); };

  AC.onRoute = async () => {
    S.route = AC.parseRoute();
    if (S.route.name === "catalog" && S.route.id) {
      const browse = AC.viewOf("browse_pick");
      const cache = browse ? AC.entityCache(browse.entity) : null;
      if (cache) for (let n = cache.nodes[S.route.id]; n; n = n.parent) S.open.add(n.id);
    }
    $("#page").scrollTop = 0;
    if (window.matchMedia("(max-width:900px)").matches) $("#app").classList.remove("nav-open");
    AC.render();
    try {
      if (S.route.name === "mine" && S.me) await AC.loadMine();
      if (S.route.name === "review" && S.me) await AC.loadReport();
      if (S.route.name.startsWith("admin")) {
        if (S.me && S.me.is_admin) await AC.loadQueue().catch(() => {});
        if (S.route.name === "admin-users") await AC.loadUsers().catch(() => {});
      }
      AC.render();
    } catch (e) { AC.fail(e); }
  };

  // ---------------------------------------------------------------- events
  const bind = () => {
    document.addEventListener("click", (e) => {
      const t = e.target.closest("[data-act]");
      if (!t) return;
      const fn = AC.act[t.dataset.act];
      if (fn) { const r = fn(t, e); if (r && r.catch) r.catch(AC.fail); }
    });
    document.addEventListener("input", (e) => { const fn = AC.inputs[e.target.id]; if (fn) fn(e.target, e); });
    document.addEventListener("change", (e) => { const fn = AC.changes[e.target.id] || AC.changes[e.target.dataset.change]; if (fn) fn(e.target, e); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("#sheet").hidden) AC.sheet.close();
      if (e.key === "Enter" && /^(p-name|p-email|p-pass)$/.test(e.target.id)) {
        e.preventDefault();
        AC.act["page-auth-go"]({ disabled: false });
      }
    });
    for (const ev of ["dragstart", "dragover", "dragleave", "drop", "dragend"]) {
      document.addEventListener(ev, (e) => AC.dnd[ev] && AC.dnd[ev](e));
    }
    window.addEventListener("hashchange", AC.onRoute);
    $("#scrim").addEventListener("click", AC.sheet.close);
    $("#navbtn").addEventListener("click", () => $("#app").classList.toggle("nav-open"));
    $("#theme-toggle").addEventListener("click", AC.toggleTheme);
    $("#who").addEventListener("click", () => AC.act.account());
    $("#gs").addEventListener("input", (e) => {
      S.q = e.target.value;
      if (S.route.name !== "catalog" || S.route.id) location.hash = "#/catalog";
      else AC.renderPage();
    });
  };

  AC.act.sec = (t) => { S.navClosed[t.dataset.sec] = !S.navClosed[t.dataset.sec]; AC.renderNav(); };
  AC.act["node-toggle"] = (t) => { const id = t.dataset.id; S.open.has(id) ? S.open.delete(id) : S.open.add(id); AC.renderNav(); };
  AC.act["sheet-close"] = () => AC.sheet.close();

  AC.setTheme = (theme) => {
    const valid = theme === "light" || theme === "dark";
    const t = valid ? theme : "light";
    if (valid) AC.store.set("ac.theme", theme);
    document.documentElement.setAttribute("data-theme", t);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.setAttribute("aria-pressed", String(t === "dark"));
      btn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
      btn.title = btn.getAttribute("aria-label");
      const use = btn.querySelector("use");
      if (use) use.setAttribute("href", t === "dark" ? "#i-sun" : "#i-moon");
    }
  };
  AC.toggleTheme = () => {
    const current = document.documentElement.getAttribute("data-theme");
    AC.setTheme(current === "light" ? "dark" : "light");
  };

  AC.applyBrand = () => {
    const title = (S.meta && S.meta.title) || (AC.spec && AC.spec.app && AC.spec.app.title) || "schemasmith";
    document.title = title;
    const brand = document.querySelector(".brand-name");
    if (brand) brand.textContent = title;
  };

  AC.boot = async () => {
    S.token = AC.store.get("ac.token") || "";
    S.sel = new Set(AC.store.get("ac.sel") || []);
    // Light first, whatever the OS prefers; dark only when the user chose it here.
    const saved = AC.store.get("ac.theme");
    AC.setTheme(saved === "dark" ? "dark" : "light");
    if (window.matchMedia("(max-width:900px)").matches) $("#app").classList.remove("nav-open");
    bind();
    S.route = AC.parseRoute();
    AC.render();
    try {
      await AC.loadSpec().catch(() => null);
      AC.applyBrand();
      await Promise.all([AC.loadBrowseData().catch(() => {}), AC.refreshMe().catch(() => {}), AC.loadMeta()]);
      AC.applyBrand();
      await AC.onRoute();
    } catch (e) { AC.fail(e); AC.render(); }
    // Light polling so an admin sees new records and a requester sees decisions without reloading.
    setInterval(async () => {
      if (document.hidden || !S.me || !$("#sheet").hidden) return;
      try {
        const openIds = [...(S.mineOpen || [])];
        const snap = () => JSON.stringify([S.mine, S.queue, S.reportRows,
          openIds.map((id) => S.comments[id] || null)]);
        const before = snap();
        await AC.loadMine();
        if (S.me.is_admin) await AC.loadQueue().catch(() => {});
        if (S.route.name === "review") await AC.loadReport().catch(() => {});
        for (const id of openIds) {
          const ent = AC.myEntity() || AC.queueEntity();
          if (ent) { try { await AC.loadComments(ent, id, true); } catch { /* keep old thread */ } }
        }
        if (snap() !== before) AC.render();
      } catch { /* transient */ }
    }, 20000);
  };
})();
