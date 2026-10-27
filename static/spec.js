/* schemasmith: spec-driven app model. Fetches /api/spec, builds AC.spec. No dependencies. */
(() => {
  const AC = (window.AC = window.AC || {});
  const S = (AC.S = AC.S || {});

  const VIEW_ROUTES = {
    browse_pick: "catalog",
    my_records: "mine",
    queue: "admin-requests",
    report: "review",
    reference_editor: "admin-catalog",
    exports: "admin-allowlist",
    dashboard: "admin-overview",
  };

  AC.spec = null;

  const hasRole = (me, roles) => {
    if (!roles || !roles.length) return true;
    if (!me) return false;
    const mine = new Set([...(me.roles || []), ...(me.is_admin ? ["admin"] : []), ...(me.is_reviewer ? ["reviewer"] : [])]);
    return roles.some((r) => mine.has(r));
  };
  AC.hasRole = hasRole;

  AC.entityOf = (name) => (AC.spec && AC.spec.entities ? AC.spec.entities[name] : null);

  AC.viewOf = (type) => {
    const views = (AC.spec && AC.spec.views) || [];
    return views.find((v) => v && v.type === type) || null;
  };

  AC.viewsOf = (type) => {
    const views = (AC.spec && AC.spec.views) || [];
    return views.filter((v) => v && v.type === type);
  };

  // First view of each type that the current user may see, in spec order.
  AC.navViews = () => {
    const views = (AC.spec && AC.spec.views) || [];
    const seen = new Set();
    return views.filter((v) => {
      if (!v || !v.type || seen.has(v.type)) return false;
      if (!hasRole(S.me, v.roles)) return false;
      seen.add(v.type);
      return true;
    });
  };

  AC.routeFor = (view) => (view && VIEW_ROUTES[view.type]) || null;

  // Route table derived from the spec; unknown routes fall back to catalog.
  AC.parseRoute = (hash) => {
    const [a, b] = String(hash || location.hash).replace(/^#\/?/, "").split("/");
    if (a === "mine" && AC.viewOf("my_records")) return { name: "mine" };
    if (a === "review" && AC.viewOf("report")) return { name: "review" };
    if (a === "design") return { name: "design" };
    if (a === "admin") {
      const rest = ["requests", "users", "allowlist", "catalog"].includes(b) ? b : "overview";
      return { name: "admin-" + rest };
    }
    return { name: "catalog", id: a === "catalog" && b ? b : null };
  };

  AC.routeHash = (name, id) => {
    if (name === "catalog") return id ? `#/catalog/${id}` : "#/catalog";
    if (name === "mine") return "#/mine";
    if (name === "review") return "#/review";
    if (name === "design") return "#/design";
    if (name === "admin-overview") return "#/admin";
    if (name && name.startsWith("admin-")) return "#/admin/" + name.slice("admin-".length);
    return "#/catalog";
  };

  AC.loadSpec = async () => {
    const res = await AC.api("/api/spec");
    const spec = (res && res.spec) || null;
    AC.schemaEdit = !!(res && res.schema_edit);
    AC.guestDesigner = !!(res && res.guest_designer);
    if (!spec) {
      AC.spec = null;
      return null;
    }
    const entities = {};
    for (const e of spec.entities || []) entities[e.name] = e;
    AC.spec = {
      raw: spec,
      app: spec.app || { title: "schemasmith" },
      roles: spec.roles || [],
      entities,
      entityList: spec.entities || [],
      workflow: spec.workflow || null,
      views: spec.views || [],
      exports: spec.exports || [],
      dashboard: spec.dashboard || [],
      schemaEdit: !!(res && res.schema_edit),
      designerConfigured: !!(res && res.designer_configured),
    };
    if (AC.spec.app && AC.spec.app.title && AC.meta) AC.meta.title = AC.spec.app.title;
    return AC.spec;
  };
})();
