frappe.pages["bi_studio"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: __("BI Studio"),
    single_column: true,
  });

  wrapper.bi_studio = new BIStudioApp(wrapper);
};

frappe.pages["bi_studio"].on_page_show = function (wrapper) {
  wrapper.bi_studio && wrapper.bi_studio.show();
};

class BIStudioApp {
  constructor(wrapper) {
    this.wrapper = wrapper;
    this.page = wrapper.page;
    this.state = {
      collapsed: false,
      activeTab: "overview",
      datasets: [],
      dashboards: [],
      favorites: [],
      listFilters: {
        datasets: { search: "", period: "all", sort: "date_desc" },
        dashboards: { search: "", period: "all", sort: "date_desc" },
        favorites: { search: "", period: "all", sort: "date_desc" },
      },
      selectedWidget: null,
      builder: { dashboard: null, dataset: null, columns: [], widgets: [] },
      };
      this.chartInstances = [];
      this.dashboardV1Renderer = null;
      this.dashboardV1AssetPromise = null;
      this.make();
    }

  make() {
    this.activateDeskSidebar();
    this.$root = $(`<div class="bi-studio-shell"></div>`).appendTo($(this.wrapper).find(".page-content").empty());
    this.$root.html(`<main class="bi-content"></main>`);
    this.$sidebar = $();
    this.$content = this.$root.find(".bi-content");
    this.bindEvents();
    this.renderSidebar();
    this.show();
  }

  bindEvents() {
    this.$root.on("click", "[data-route]", (event) => {
      event.preventDefault();
      this.navigate($(event.currentTarget).attr("data-route"));
    });

    $(document)
      .off("click.bi-studio-route")
      .on("click.bi-studio-route", "a[href*='/desk/bi_studio']", (event) => {
        const href = event.currentTarget.getAttribute("href") || "";
        const path = this.routePathFromHref(href);
        if (path === null) return;

        event.preventDefault();
        event.stopImmediatePropagation();
        this.navigate(path);
      });

    this.$root.on("click", "[data-action]", (event) => {
      const $target = $(event.currentTarget);
      const action = $target.attr("data-action");
      const name = $target.attr("data-name");
      const type = $target.attr("data-type");
      this.handleAction(action, name, type, $target);
    });

    this.$root.on("input", "[data-action='local-search']", (event) => {
      const value = $(event.currentTarget).val().toLowerCase();
      this.$content.find(".bi-table tbody tr").each((_, row) => {
        $(row).toggle($(row).text().toLowerCase().includes(value));
      });
    });

    this.$root.on("input change", "[data-list-filter]", (event) => {
      const $target = $(event.currentTarget);
      this.updateListFilter($target.attr("data-list"), $target.attr("data-field"), $target.val());
    });

    this.$root.on("change", "#bi-builder-dataset", (event) => {
      this.syncBuilderForm();
      this.loadBuilderDataset($(event.currentTarget).val()).then(() => this.paintBuilder());
    });

    this.$root.on("input change", "[data-builder-field]", () => {
      this.syncBuilderForm();
    });

    $(document).on("keydown.bi-studio", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        this.openSearch();
      }
    });

    frappe.router.on("change", () => {
      const route = frappe.get_route() || [];
      $("body").toggleClass("bi-studio-active", route[0] === "bi_studio");
    });
  }

  show() {
    this.activateDeskSidebar();
    this.page.set_title(__("BI Studio"));
    this.renderSidebar();
    const route = this.getRoute();
    if (route[0] === "admin" && !this.isAdmin()) {
      this.renderNoAccess();
      return;
    }

    const [section, param] = route;
    if (!section) return this.renderHome();
    if (section === "import") {
      frappe.set_route("bi_intelligent_pipeline");
      return;
    }
    if (section === "datasets") return this.renderDatasets();
    if (section === "dataset") return this.renderDatasetDetail(param);
    if (section === "dashboards") return this.renderDashboards();
    if (section === "dashboard") return this.renderDashboardViewer(param);
    if (section === "dashboard-builder") return this.renderDashboardBuilder(param);
    if (section === "ai-analyses") return this.renderAIAnalyses();
    if (section === "ai-analysis") return this.renderAIAnalysisDetail(param);
    if (section === "favorites") return this.renderFavorites();
    if (section === "admin") return this.renderAdminRoute(route[1]);
    return this.renderHome();
  }

  getRoute() {
    const route = frappe.get_route() || [];
    return route.slice(1);
  }

  getPath() {
    return this.getRoute().join("/");
  }

  navigate(path) {
    const parts = this.normalizeRoutePath(path).split("/").filter(Boolean);
    frappe.set_route("bi_studio", ...parts);
  }

  normalizeRoutePath(path) {
    return String(path || "")
      .replace(/^https?:\/\/[^/]+\/desk\/bi_studio\/?/i, "")
      .replace(/^\/desk\/bi_studio\/?/i, "")
      .replace(/^\/+/, "");
  }

  routePathFromHref(href) {
    let url;
    try {
      url = new URL(href, window.location.origin);
    } catch (error) {
      return null;
    }
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/desk/bi_studio")) {
      return null;
    }
    return this.normalizeRoutePath(url.pathname);
  }

  isAdmin() {
    return frappe.session.user === "Administrator" || frappe.user.has_role("System Manager");
  }

  getSidebarItems() {
    const adminDependsOn = "frappe.session.user === 'Administrator' || frappe.user.has_role('System Manager')";
    return [
      { label: "BI Studio", type: "Link", link_type: "URL", url: "/desk/bi_studio", icon: "bar-chart-3", child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Générateur IA", type: "Link", link_type: "URL", url: "/desk/bi_intelligent_pipeline", icon: "sparkles", child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Mes donn\u00e9es", type: "Section Break", child: 0, collapsible: 1, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Datasets", type: "Link", link_type: "URL", url: "/desk/bi_studio/datasets", icon: "database", child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Tableaux de bord", type: "Link", link_type: "URL", url: "/desk/bi_studio/dashboards", icon: "layout-dashboard", child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "AI Analyses", type: "Link", link_type: "URL", url: "/desk/bi_studio/ai-analyses", icon: "sparkles", child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Favorites", type: "Link", link_type: "URL", url: "/desk/bi_studio/favorites", icon: "star", child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Administration", type: "Section Break", display_depends_on: adminDependsOn, child: 0, collapsible: 1, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Admin Dashboard", type: "Link", link_type: "URL", url: "/desk/bi_studio/admin", icon: "activity", display_depends_on: adminDependsOn, child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "All Datasets", type: "Link", link_type: "URL", url: "/desk/bi_studio/admin/datasets", icon: "database", display_depends_on: adminDependsOn, child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "All Dashboards", type: "Link", link_type: "URL", url: "/desk/bi_studio/admin/dashboards", icon: "layout-dashboard", display_depends_on: adminDependsOn, child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "All AI Analyses", type: "Link", link_type: "URL", url: "/desk/bi_studio/admin/ai-analyses", icon: "sparkles", display_depends_on: adminDependsOn, child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
      { label: "Import Jobs", type: "Link", link_type: "URL", url: "/desk/bi_studio/admin/import-jobs", icon: "upload-cloud", display_depends_on: adminDependsOn, child: 0, collapsible: 0, indent: 0, keep_closed: 0, show_arrow: 0 },
    ];
  }

  ensureDeskSidebarConfig() {
    frappe.boot.workspace_sidebar_item = frappe.boot.workspace_sidebar_item || {};
    const key = "bi studio";
    const current = frappe.boot.workspace_sidebar_item[key];
    const items = this.getSidebarItems();
    if (!current || !Array.isArray(current.items) || current.items.length === 0) {
      frappe.boot.workspace_sidebar_item[key] = {
        label: "BI Studio",
        items,
        header_icon: "bar-chart-3",
        module: "Desk",
        app: "bi_studio",
      };
      return;
    }

    const aiItem = items.find((item) => item.url === "/desk/bi_intelligent_pipeline");
    const hasAiItem = current.items.some((item) => item.url === "/desk/bi_intelligent_pipeline");
    if (aiItem && !hasAiItem) {
      current.items.splice(1, 0, aiItem);
    }
    // Drop the deprecated standalone import wizard if it's still cached in boot
    const legacyIndex = current.items.findIndex((item) => item.url === "/desk/bi_studio/import");
    if (legacyIndex >= 0) current.items.splice(legacyIndex, 1);
  }

  icon(name, size = "sm") {
    try {
      return frappe.utils.icon(name, size);
    } catch (error) {
      return "";
    }
  }

    call(method, args = {}) {
      return frappe.call({ method, args }).then((response) => response.message);
    }

    unmountDashboardV1Renderer() {
      if (this.dashboardV1Renderer && typeof this.dashboardV1Renderer.unmount === "function") {
        this.dashboardV1Renderer.unmount();
      }
      this.dashboardV1Renderer = null;
    }

    loadDashboardV1Renderer() {
      if (window.BIStudioDashboardV1Renderer) return Promise.resolve(window.BIStudioDashboardV1Renderer);
      if (this.dashboardV1AssetPromise) return this.dashboardV1AssetPromise;

      const loadScript = (src) => {
        window.__biStudioDashboardV1AssetPromises = window.__biStudioDashboardV1AssetPromises || {};
        if (window.__biStudioDashboardV1AssetPromises[src]) return window.__biStudioDashboardV1AssetPromises[src];
        window.__biStudioDashboardV1AssetPromises[src] = new Promise((resolve, reject) => {
          if (src.includes("dashboard_v1_utils") && window.BIStudioDashboardV1Utils) return resolve();
          if (src.includes("dashboard_v1_renderer") && window.BIStudioDashboardV1Renderer) return resolve();
          const script = document.createElement("script");
          script.src = src;
          script.async = true;
          script.setAttribute("data-bi-dashboard-v1-src", src);
          script.onload = resolve;
          script.onerror = () => reject(new Error(`Impossible de charger ${src}`));
          document.head.appendChild(script);
        });
        return window.__biStudioDashboardV1AssetPromises[src];
      };

      this.dashboardV1AssetPromise = loadScript("/assets/bi_studio/js/dashboard_v1_utils.js")
        .then(() => loadScript("/assets/bi_studio/js/dashboard_v1_renderer.js"))
        .then(() => window.BIStudioDashboardV1Renderer);
      return this.dashboardV1AssetPromise;
    }

  renderSidebar() {
    this.activateDeskSidebar();
  }

  activateDeskSidebar() {
    $("body").addClass("bi-studio-active");
    if (frappe.app && frappe.app.sidebar) {
      this.ensureDeskSidebarConfig();
      frappe.app.sidebar.setup("BI Studio");
      frappe.app.sidebar.wrapper.show();
      frappe.app.sidebar.set_active_workspace_item();
    }
  }

  isActive(path, route) {
    if (!route) return !path;
    return path === route || path.startsWith(`${route}/`);
  }

  header(title, subtitle, actions = "") {
    return `
      <div class="bi-page-head">
        <div>
          <h2 class="bi-page-title">${this.escape(title)}</h2>
          <p class="bi-page-subtitle">${this.escape(subtitle || "")}</p>
        </div>
        <div class="bi-actions">${actions}</div>
      </div>
    `;
  }

    skeleton(title = "Chargement") {
      this.unmountDashboardV1Renderer();
      this.$content.html(this.header(title, "Récupération des données") + `<div class="bi-skeleton"></div>`);
    }

  listToolbar(list, placeholder) {
    const filters = this.state.listFilters[list] || { search: "", period: "all", sort: "date_desc" };
    return `
      <div class="bi-filter-toolbar">
        <div class="bi-search-field">
          ${this.icon("search")}
          <input
            class="bi-input"
            data-list-filter
            data-list="${this.escapeAttr(list)}"
            data-field="search"
            value="${this.escapeAttr(filters.search)}"
            placeholder="${this.escapeAttr(placeholder || __("Rechercher"))}"
          >
        </div>
        <select class="bi-select" data-list-filter data-list="${this.escapeAttr(list)}" data-field="period">
          ${this.option("all", __("Toutes les dates"), filters.period)}
          ${this.option("today", __("Aujourd'hui"), filters.period)}
          ${this.option("7d", __("7 derniers jours"), filters.period)}
          ${this.option("30d", __("30 derniers jours"), filters.period)}
          ${this.option("year", __("Cette année"), filters.period)}
        </select>
        <select class="bi-select" data-list-filter data-list="${this.escapeAttr(list)}" data-field="sort">
          ${this.option("date_desc", __("Date récente"), filters.sort)}
          ${this.option("date_asc", __("Date ancienne"), filters.sort)}
          ${this.option("alpha_asc", __("Alphabet A-Z"), filters.sort)}
          ${this.option("alpha_desc", __("Alphabet Z-A"), filters.sort)}
          ${this.option("size_desc", __("Taille grande"), filters.sort)}
          ${this.option("size_asc", __("Taille petite"), filters.sort)}
        </select>
      </div>
    `;
  }

  option(value, label, selected) {
    return `<option value="${this.escapeAttr(value)}" ${value === selected ? "selected" : ""}>${this.escape(label)}</option>`;
  }

  updateListFilter(list, field, value) {
    if (!this.state.listFilters[list]) return;
    this.state.listFilters[list][field] = value || "";
    this.renderListResults(list);
  }

  renderListResults(list) {
    const rows = this.filteredRows(list, this.state[list] || []);
    const html = {
      datasets: () => this.renderDatasetsTable(rows),
      dashboards: () => this.renderDashboardTable(rows),
      favorites: () => this.renderFavoritesTable(rows),
    }[list];
    if (html) {
      this.$content.find(`[data-list-results="${list}"]`).html(html());
    }
  }

  filteredRows(list, rows) {
    const filters = this.state.listFilters[list] || {};
    const search = String(filters.search || "").trim().toLowerCase();
    const filtered = (rows || []).filter((row) => {
      if (search && !this.getListSearchText(list, row).includes(search)) return false;
      return this.isInsidePeriod(this.getListDate(list, row), filters.period);
    });

    const collator = new Intl.Collator(frappe.boot.lang || "fr-FR", { numeric: true, sensitivity: "base" });
    const sort = filters.sort || "date_desc";
    return filtered.sort((a, b) => {
      if (sort === "alpha_asc") return collator.compare(this.getListLabel(list, a), this.getListLabel(list, b));
      if (sort === "alpha_desc") return collator.compare(this.getListLabel(list, b), this.getListLabel(list, a));
      if (sort === "size_asc") return this.getListSize(list, a) - this.getListSize(list, b);
      if (sort === "size_desc") return this.getListSize(list, b) - this.getListSize(list, a);

      const left = this.toTimestamp(this.getListDate(list, a));
      const right = this.toTimestamp(this.getListDate(list, b));
      return sort === "date_asc" ? left - right : right - left;
    });
  }

  getListLabel(list, row) {
    if (list === "datasets") return row.dataset_name || row.name || "";
    if (list === "dashboards") return row.dashboard_name || row.name || "";
    return row.label || row.reference_name || row.name || "";
  }

  getListSearchText(list, row) {
    const values = {
      datasets: [row.dataset_name, row.status, row.owner, row.row_count, row.column_count],
      dashboards: [row.dashboard_name, row.dataset_name, row.dataset, row.dashboard_type, row.owner, row.widget_count],
      favorites: [row.reference_doctype, row.label, row.reference_name, row.reference_owner, row.size_label],
    }[list] || Object.values(row || {});
    return values.map((value) => String(value || "").toLowerCase()).join(" ");
  }

  getListDate(list, row) {
    if (list === "datasets") return row.imported_at || row.modified;
    if (list === "dashboards") return row.modified || row.created_at;
    return row.favorited_at;
  }

  getListSize(list, row) {
    if (list === "datasets") return Number(row.row_count || 0);
    if (list === "dashboards") return Number(row.widget_count || 0);
    return Number(row.size_value || 0);
  }

  isInsidePeriod(value, period) {
    if (!period || period === "all") return true;
    const date = this.toDate(value);
    if (!date) return false;
    const now = new Date();
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);

    if (period === "today") return date >= start;
    if (period === "7d") {
      start.setDate(start.getDate() - 6);
      return date >= start;
    }
    if (period === "30d") {
      start.setDate(start.getDate() - 29);
      return date >= start;
    }
    if (period === "year") {
      start.setMonth(0, 1);
      return date >= start;
    }
    return true;
  }

  toDate(value) {
    if (!value) return null;
    const date = new Date(String(value).replace(" ", "T"));
    return Number.isNaN(date.getTime()) ? null : date;
  }

  toTimestamp(value) {
    const date = this.toDate(value);
    return date ? date.getTime() : 0;
  }

  renderNoAccess() {
    this.$content.html(this.header("Accès refusé", "Cette page est réservée aux administrateurs.") + `<div class="bi-empty">Vous n'avez pas accès à cette page.</div>`);
  }

  renderHome() {
    this.skeleton("BI Studio");
    Promise.all([
      this.call("bi_studio.api.dataset.get_datasets"),
      this.call("bi_studio.api.dashboard.get_dashboards"),
      this.call("bi_studio.api.ai.get_ai_analyses"),
    ]).then(([datasets, dashboards, analyses]) => {
      this.$content.html(`
        ${this.header("BI Studio", "Importez un fichier Excel, laissez l'IA générer un tableau de bord prêt à l'emploi.", `
          <button class="btn btn-primary" data-action="open-ai-pipeline">${this.icon("sparkles")} ${__("Générer avec IA")}</button>
          <button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Actualiser")}</button>
        `)}
        ${this.heroAiCard()}
        <div class="bi-kpi-grid">
          ${this.statCard(datasets.length, "Datasets", "database")}
          ${this.statCard(dashboards.length, "Tableaux de bord", "layout-dashboard")}
          ${this.statCard(analyses.length, "Analyses IA", "sparkles")}
          ${this.statCard(datasets.filter((d) => d.status === "Ready").length, "Datasets prêts", "check-circle")}
          ${this.statCard(dashboards.filter((d) => d.is_system_suggested).length, "Dashboards suggérés", "star")}
        </div>
        <div class="bi-grid">
          ${this.summaryListCard("Derniers datasets", datasets, "dataset", "dataset_name")}
          ${this.summaryListCard("Derniers tableaux de bord", dashboards, "dashboard", "dashboard_name")}
        </div>
      `);
    });
  }

  summaryListCard(title, rows, route, labelField) {
    const body = rows.length ? rows.slice(0, 6).map((row) => `
      <tr>
        <td>${this.escape(row[labelField] || row.name)}</td>
        <td class="text-right"><button class="btn btn-xs btn-default" data-route="${route}/${row.name}">${__("Consulter")}</button></td>
      </tr>
    `).join("") : `<tr><td colspan="2" class="text-muted">${__("Aucun élément")}</td></tr>`;
    return `
      <div class="bi-card">
        <div class="bi-card-header"><h3 class="bi-card-title">${this.escape(title)}</h3></div>
        <div class="bi-table-wrap"><table class="bi-table"><tbody>${body}</tbody></table></div>
      </div>
    `;
  }

  kpiCard(value, label) {
    return `
      <div class="bi-card bi-kpi-card">
        <div class="bi-kpi-value">${this.formatNumber(value)}</div>
        <div class="bi-kpi-label">${this.escape(label)}</div>
      </div>
    `;
  }

  heroAiCard() {
    return `
      <div class="bi-hero-ai">
        <div class="bi-hero-ai-content">
          <div class="bi-hero-ai-eyebrow">${this.icon("sparkles")} ${__("Pipeline IA")}</div>
          <h2 class="bi-hero-ai-title">${__("De votre Excel au tableau de bord, en un clic.")}</h2>
          <p class="bi-hero-ai-text">${__("Importez un fichier, l'IA profile vos données, calcule les KPIs et compose votre tableau de bord.")}</p>
          <div class="bi-hero-ai-actions">
            <button class="btn btn-primary" data-action="open-ai-pipeline">${this.icon("upload-cloud")} ${__("Lancer le pipeline IA")}</button>
            <button class="btn btn-default" data-route="dashboards">${this.icon("layout-dashboard")} ${__("Voir les tableaux existants")}</button>
          </div>
        </div>
        <div class="bi-hero-ai-orb" aria-hidden="true"></div>
      </div>
    `;
  }

  statCard(value, label, iconName) {
    return `
      <div class="bi-card bi-stat-card">
        <div class="bi-stat-icon">${this.icon(iconName || "bar-chart-3")}</div>
        <div class="bi-stat-meta">
          <div class="bi-stat-value">${this.formatNumber(value)}</div>
          <div class="bi-stat-label">${this.escape(label)}</div>
        </div>
      </div>
    `;
  }

  renderDatasets() {
    this.skeleton("Datasets");
    this.call("bi_studio.api.dataset.get_datasets").then((rows) => {
      this.state.datasets = rows;
      this.$content.html(`
        ${this.header("Datasets", "Données importées, nettoyées et figées après import.", `
          <button class="btn btn-primary" data-route="import">${this.icon("upload")} ${__("Importer fichier")}</button>
          <button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>
        `)}
        <div class="bi-card">
          ${this.listToolbar("datasets", __("Rechercher un dataset"))}
          <div data-list-results="datasets">${this.renderDatasetsTable(this.filteredRows("datasets", rows))}</div>
        </div>
      `);
    });
  }

  renderDatasetsTable(rows) {
    if (!rows.length) return `<div class="bi-empty">Aucun dataset. Importez un fichier Excel pour commencer.</div>`;
    return `
      <div class="bi-table-wrap">
        <table class="bi-table">
          <thead>
            <tr>
              <th>${__("Nom du dataset")}</th>
              <th>${__("Statut")}</th>
              <th>${__("Propriétaire")}</th>
              <th class="is-number">${__("Lignes")}</th>
              <th class="is-number">${__("Colonnes")}</th>
              <th>${__("Date d'import")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td>${this.favoriteButton("BI Dataset", row.name, row.is_favorite)} ${this.escape(row.dataset_name)}</td>
                <td>${this.badge(row.status)}</td>
                <td>${this.escape(row.owner || "")}</td>
                <td class="is-number">${this.formatNumber(row.row_count)}</td>
                <td class="is-number">${this.formatNumber(row.column_count)}</td>
                <td>${this.formatDate(row.imported_at)}</td>
                <td class="text-right">
                  <button class="btn btn-xs btn-default" data-route="dataset/${row.name}">${__("Consulter")}</button>
                  <button class="btn btn-xs btn-default" data-action="rename-dataset" data-name="${row.name}">${__("Renommer")}</button>
                  <button class="btn btn-xs btn-default" data-action="export-dataset" data-name="${row.name}">${__("Exporter")}</button>
                  <button class="btn btn-xs btn-default" data-route="dashboard-builder/new/${row.name}">${__("+ Dashboard")}</button>
                  <button class="btn btn-xs btn-danger" data-action="delete-dataset" data-name="${row.name}">${__("Supprimer")}</button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  renderDatasetDetail(datasetName) {
    if (!datasetName) return this.navigate("datasets");
    this.skeleton("Dataset");
    this.call("bi_studio.api.dataset.get_dataset_detail", { dataset_name: datasetName }).then((data) => {
      const dataset = data.dataset;
      this.$content.html(`
        ${this.header(dataset.dataset_name, `${dataset.row_count || 0} lignes, ${dataset.column_count || 0} colonnes`, `
          ${this.favoriteButton("BI Dataset", dataset.name, data.is_favorite)}
          <button class="btn btn-default" data-action="rename-dataset" data-name="${dataset.name}">${__("Renommer")}</button>
          <button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>
          <button class="btn btn-default" data-action="export-dataset" data-name="${dataset.name}">${__("Exporter .xlsx")}</button>
          <button class="btn btn-primary" data-route="dashboard-builder/new/${dataset.name}">${__("Créer dashboard")}</button>
          ${dataset.suggested_dashboard ? `<button class="btn btn-default" data-route="dashboard/${dataset.suggested_dashboard}">${__("Voir dashboard suggéré")}</button>` : ""}
          <button class="btn btn-danger" data-action="delete-dataset" data-name="${dataset.name}">${__("Supprimer")}</button>
        `)}
        <div class="bi-tabs">
          ${["overview", "clean", "kpis", "charts", "suggested"].map((tab) => `<button class="bi-tab ${this.state.activeTab === tab ? "is-active" : ""}" data-action="dataset-tab" data-name="${tab}">${this.tabLabel(tab)}</button>`).join("")}
        </div>
        <div class="bi-dataset-tab">${this.datasetTab(data)}</div>
      `);
      this.currentDatasetDetail = data;
    });
  }

  tabLabel(tab) {
    return { overview: "Aperçu", clean: "Données nettoyées", kpis: "KPI", charts: "Graphiques", suggested: "Dashboard suggéré" }[tab] || tab;
  }

  datasetTab(data) {
    if (this.state.activeTab === "clean" || this.state.activeTab === "overview") {
      return `<div class="bi-card">${this.renderCleanTable(data.cleaned_data)}</div>`;
    }
    if (this.state.activeTab === "kpis") {
      return `<div class="bi-kpi-grid">${data.kpis.map((kpi) => this.kpiCard(kpi.value, kpi.label)).join("") || `<div class="bi-empty">Aucun KPI automatique.</div>`}</div>`;
    }
    if (this.state.activeTab === "charts") {
      setTimeout(() => data.charts.forEach((chart) => this.drawChart(chart.name, chart.chart_type, chart.data)), 50);
      return `<div class="bi-chart-grid">${data.charts.map((chart) => this.chartShell(chart.name, chart.title)).join("") || `<div class="bi-empty">Aucun graphique automatique.</div>`}</div>`;
    }
    if (this.state.activeTab === "suggested") {
      return data.suggested_dashboard
        ? `<div class="bi-card"><p>${__("Un dashboard suggéré a été enregistré automatiquement après l'import.")}</p><button class="btn btn-primary" data-route="dashboard/${data.suggested_dashboard}">${__("Ouvrir le dashboard suggéré")}</button></div>`
        : `<div class="bi-empty">Aucun dashboard suggéré disponible.</div>`;
    }
    return "";
  }

  renderCleanTable(data) {
    const columns = data.columns || [];
    const rows = data.rows || [];
    if (!rows.length) return `<div class="bi-empty">Aucune donnée à afficher.</div>`;
    return `
      <div class="bi-card-header">
        <div>
          <h3 class="bi-card-title">${__("Données nettoyées")}</h3>
          <div class="bi-muted">${this.formatNumber(data.total)} lignes au total</div>
        </div>
      </div>
      <div class="bi-table-wrap">
        <table class="bi-table">
          <thead><tr>${columns.map((column) => `<th class="${column.is_numeric ? "is-number" : ""}">${this.escape(column.column_label)}</th>`).join("")}</tr></thead>
          <tbody>
            ${rows.map((row) => `<tr>${columns.map((column) => `<td class="${column.is_numeric ? "is-number" : ""}">${this.escape(row[column.column_name])}</td>`).join("")}</tr>`).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  renderDashboards() {
    this.skeleton("Tableaux de bord");
    this.call("bi_studio.api.dashboard.get_dashboards").then((rows) => {
      this.state.dashboards = rows;
      this.$content.html(`
        ${this.header("Tableaux de bord", "Dashboards suggérés et dashboards créés par l'utilisateur.", `
          <button class="btn btn-primary" data-route="dashboard-builder/new">${this.icon("plus")} ${__("Nouveau dashboard")}</button>
          <button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>
        `)}
        <div class="bi-card">
          ${this.listToolbar("dashboards", __("Rechercher un tableau de bord"))}
          <div data-list-results="dashboards">${this.renderDashboardTable(this.filteredRows("dashboards", rows))}</div>
        </div>
      `);
    });
  }

  renderDashboardTable(rows) {
    if (!rows.length) return `<div class="bi-empty">Aucun tableau de bord.</div>`;
    return `
      <div class="bi-table-wrap">
        <table class="bi-table">
          <thead><tr><th>${__("Nom")}</th><th>${__("Dataset")}</th><th>${__("Type")}</th><th class="is-number">${__("Widgets")}</th><th>${__("Date")}</th><th>${__("Propriétaire")}</th><th></th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td>${this.favoriteButton("BI Dashboard", row.name, row.is_favorite)} ${this.escape(row.dashboard_name)}</td>
                <td>${this.escape(row.dataset_name || row.dataset)}</td>
                <td>${this.escape(row.dashboard_type)}</td>
                <td class="is-number">${this.formatNumber(row.widget_count || 0)}</td>
                <td>${this.formatDate(row.modified || row.created_at)}</td>
                <td>${this.escape(row.owner)}</td>
                <td class="text-right">
                  <button class="btn btn-xs btn-default" data-route="dashboard/${row.name}">${__("Consulter")}</button>
                  <button class="btn btn-xs btn-default" data-route="dashboard-builder/${row.name}">${__("Modifier")}</button>
                  <button class="btn btn-xs btn-default" data-action="rename-dashboard" data-name="${row.name}">${__("Renommer")}</button>
                  <button class="btn btn-xs btn-danger" data-action="delete-dashboard" data-name="${row.name}">${__("Supprimer")}</button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

    renderDashboardViewer(dashboardName) {
      if (!dashboardName) return this.navigate("dashboards");
      this.skeleton("Dashboard");
      this.call("bi_studio.api.dashboard_builder.get_intelligent_dashboard", { dashboard_name: dashboardName })
        .then((data) => {
          if (this.isDashboardV1Payload(data)) {
            return this.renderDashboardV1Viewer(data);
          }
          return this.renderLegacyDashboardViewer(dashboardName);
        })
        .catch(() => this.renderLegacyDashboardViewer(dashboardName));
    }

    isDashboardV1Payload(data) {
      const layout = data && data.layout;
      const widgets = (data && data.widgets) || [];
      const allowedTypes = ["kpi_card", "bar_chart", "line_chart", "pie_chart", "data_table", "filter"];
      const hasV1Widget = widgets.some((widget) => {
        const configType = widget.config && widget.config.type;
        return allowedTypes.includes(widget.widget_type) || allowedTypes.includes(configType);
      });
      return Boolean(
        data &&
          (data.ai_spec ||
            hasV1Widget ||
            (layout && layout.columns === 12 && layout.row_height === 80))
      );
    }

    renderDashboardV1Viewer(data) {
      this.unmountDashboardV1Renderer();
      this.$content.html(`<div id="bi-dashboard-v1-viewer" class="bi-dashboard-v1-viewer"></div>`);
      const mount = this.$content.find("#bi-dashboard-v1-viewer")[0];
      const actions = [
        data.ai_spec ? { key: "show-json", label: "Voir JSON" } : null,
        { key: "refresh", label: "Refresh" },
        { key: "edit", label: "Modifier" },
        { key: "export", label: "Exporter PNG" },
        { key: "delete", label: "Supprimer" },
      ].filter(Boolean);

      this.loadDashboardV1Renderer()
        .then((renderer) =>
          renderer.mount(mount, data, {
            quality_score: data.quality_score,
            status: data.status || "Publié",
            actions,
            onAction: (key) => {
              if (key === "show-json") return this.showAiJson(data.ai_spec);
              if (key === "refresh") return this.renderDashboardViewer(data.name);
              if (key === "edit") return this.navigate(`dashboard-builder/${data.name}`);
              if (key === "export") return this.exportDashboard(data.name);
              if (key === "delete") {
                return this.confirmDelete("ce dashboard", "bi_studio.api.dashboard.delete_dashboard", { dashboard_name: data.name });
              }
            },
          })
        )
        .then((instance) => {
          this.dashboardV1Renderer = instance;
        })
        .catch((error) => {
          this.$content.html(`
            ${this.header("Dashboard", "Erreur de rendu")}
            <div class="bi-card"><div class="bi-empty">${this.escape(error.message || error)}</div></div>
          `);
        });
    }

    renderLegacyDashboardViewer(dashboardName) {
      this.call("bi_studio.api.dashboard.get_dashboard_detail", { dashboard_name: dashboardName }).then((data) => {
        const dashboard = data.dashboard;
        const kpis = data.widgets.filter((widget) => ["KPI", "Quality Card"].includes(widget.widget_type)).slice(0, 5);
        const charts = data.widgets.filter((widget) => widget.widget_type === "Chart");
      const tables = data.widgets.filter((widget) => widget.widget_type === "Table");
      this.$content.html(`
        ${this.header(dashboard.dashboard_name, data.dataset.dataset_name, `
          ${this.favoriteButton("BI Dashboard", dashboard.name, data.is_favorite)}
          <button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>
          <button class="btn btn-default" data-route="dashboard-builder/${dashboard.name}">${__("Modifier")}</button>
          <button class="btn btn-default" data-action="export-dashboard" data-name="${dashboard.name}">${__("Exporter PNG")}</button>
          <button class="btn btn-danger" data-action="delete-dashboard" data-name="${dashboard.name}">${__("Supprimer")}</button>
        `)}
        <section class="bi-dashboard-export-area">
          ${kpis.length ? `<div class="bi-kpi-grid">${kpis.map((kpi) => this.kpiCard(kpi.value, kpi.title)).join("")}</div>` : ""}
          ${this.renderAICard(dashboard, data.latest_analysis)}
          <div class="bi-chart-grid">${charts.map((chart) => this.chartShell(chart.id, chart.title, chart)).join("") || `<div class="bi-empty">Aucun graphique sélectionné.</div>`}</div>
          ${tables.map((table) => this.renderWidgetTable(table)).join("")}
        </section>
        `);
        setTimeout(() => charts.forEach((chart) => this.drawChart(chart.id, chart.chart_type, chart.data)), 50);
      });
    }

    renderAICard(dashboard, latestAnalysis) {
      if (!latestAnalysis) return "";
      return `
      <div class="bi-card bi-ai-card">
        <div class="bi-card-header">
          <div>
            <h3 class="bi-card-title">${__("Analyse IA")}</h3>
            <div class="bi-muted">${__("Synthèse générée par le pipeline IA lors de la création du tableau de bord.")}</div>
          </div>
        </div>
        <div class="bi-ai-result">
          <h4>${this.escape(latestAnalysis.analysis_name || "Analyse IA")}</h4>
          <p>${this.escape(latestAnalysis.summary_short || latestAnalysis.summary_detailed || "")}</p>
          <button class="btn btn-xs btn-default" data-route="ai-analysis/${latestAnalysis.name}">${__("Consulter")}</button>
        </div>
        </div>
      `;
    }

    async showAiJson(specName) {
      if (!specName) return frappe.msgprint("Aucune spécification IA disponible.");
      const resp = await frappe.call({
        method: "bi_studio.api.dashboard_builder.get_ai_spec_json",
        args: { spec_name: specName },
      });
      const json = JSON.stringify(resp.message.validated_json || resp.message.response_json, null, 2);
      const dialog = new frappe.ui.Dialog({
        title: "JSON généré",
        size: "extra-large",
        fields: [{ fieldtype: "HTML", fieldname: "json_html" }],
      });
      dialog.fields_dict.json_html.$wrapper.html(`<pre class="bi-json">${this.escape(json)}</pre>`);
      dialog.show();
    }

    chartShell(id, title, widget = {}) {
    if (widget.error) {
      return `<div class="bi-card"><h3 class="bi-card-title">${this.escape(title || "Graphique")}</h3><div class="bi-empty">${this.escape(widget.error)}</div></div>`;
    }
    return `
      <div class="bi-card" id="chart-card-${this.escapeAttr(id)}">
        <div class="bi-card-header">
          <h3 class="bi-card-title">${this.escape(title || "Graphique")}</h3>
          <button class="btn btn-xs btn-default" data-action="export-chart" data-name="${this.escapeAttr(id)}">${__("PNG")}</button>
        </div>
        <canvas class="bi-chart-canvas" id="chart-${this.escapeAttr(id)}"></canvas>
      </div>
    `;
  }

  drawChart(id, chartType, data) {
    const canvas = document.getElementById(`chart-${id}`);
    if (!canvas) return;
    this.loadChartJs().then(() => {
      if (!window.Chart) {
        $(canvas).replaceWith(this.chartFallbackTable(data));
        return;
      }
      const existing = this.chartInstances.find((chart) => chart.canvas === canvas);
      if (existing) existing.destroy();
      const type = this.mapChartType(chartType);
      const isScatter = type === "scatter";
      const config = {
        type,
        data: {
          labels: isScatter ? undefined : data.labels || [],
          datasets: [{
            label: __("Valeur"),
            data: isScatter ? data.points || [] : data.values || [],
            borderColor: "#2563eb",
            backgroundColor: this.chartColors(data.values ? data.values.length : 1, chartType),
            tension: 0.25,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: ["Pie", "Donut", "Combined"].includes(chartType) } },
          scales: type === "pie" || type === "doughnut" ? {} : { y: { beginAtZero: true }, x: isScatter ? { type: "linear" } : {} },
        },
      };
      if (chartType === "Combined") {
        config.type = "bar";
        config.data.datasets.push({
          label: __("Tendance"),
          data: data.values || [],
          type: "line",
          borderColor: "#16a34a",
          backgroundColor: "rgba(22, 163, 74, .18)",
          tension: 0.25,
        });
      }
      const chart = new Chart(canvas, config);
      this.chartInstances.push(chart);
    });
  }

  mapChartType(chartType) {
    if (chartType === "Line") return "line";
    if (chartType === "Pie") return "pie";
    if (chartType === "Scatter Plot" || chartType === "Scatter") return "scatter";
    if (chartType === "Donut" || chartType === "Gauge") return "doughnut";
    return "bar";
  }

  chartColors(count, chartType) {
    const colors = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#475467", "#c026d3"];
    if (!["Pie", "Donut", "Gauge"].includes(chartType)) return "rgba(37, 99, 235, .24)";
    return Array.from({ length: count }, (_, index) => colors[index % colors.length]);
  }

  chartFallbackTable(data) {
    const rows = (data.labels || []).map((label, index) => `<tr><td>${this.escape(label)}</td><td class="is-number">${this.formatNumber(data.values[index])}</td></tr>`).join("");
    return `<div class="bi-table-wrap"><table class="bi-table"><tbody>${rows}</tbody></table></div>`;
  }

  renderWidgetTable(widget) {
    const data = widget.data || {};
    const columns = data.columns || [];
    const rows = data.rows || [];
    if (!rows.length) return "";
    return `
      <div class="bi-card">
        <div class="bi-card-header"><h3 class="bi-card-title">${this.escape(widget.title || "Table")}</h3></div>
        ${this.renderCleanTable({ columns, rows, total: data.total || rows.length })}
      </div>
    `;
  }

  loadChartJs() {
    if (window.Chart) return Promise.resolve();
    if (window.__biChartLoading) return window.__biChartLoading;
    window.__biChartLoading = new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js";
      script.onload = resolve;
      script.onerror = resolve;
      document.head.appendChild(script);
    });
    return window.__biChartLoading;
  }

  renderDashboardBuilder(name) {
    this.skeleton("Dashboard Builder");
    const isNew = !name || name === "new";
    Promise.all([
      this.call("bi_studio.api.dataset.get_datasets"),
      isNew ? Promise.resolve(null) : this.call("bi_studio.api.dashboard.get_dashboard_detail", { dashboard_name: name }),
    ]).then(([datasets, dashboardData]) => {
      this.state.datasets = datasets;
      if (dashboardData) {
        this.state.builder = {
          dashboard: dashboardData.dashboard,
          dataset: dashboardData.dataset.name,
          columns: dashboardData.columns,
          widgets: dashboardData.widgets.map((widget) => ({ ...widget, visible: true })),
        };
      } else {
        const queryDataset = this.getRoute()[2];
        const dataset = queryDataset || (datasets[0] && datasets[0].name);
        this.state.builder = { dashboard: null, dataset, columns: [], widgets: [] };
      }
      this.loadBuilderDataset(this.state.builder.dataset).then(() => this.paintBuilder());
    });
  }

  loadBuilderDataset(dataset) {
    if (!dataset) return Promise.resolve();
    return this.call("bi_studio.api.dataset.get_dataset_detail", { dataset_name: dataset, page_length: 1 }).then((data) => {
      this.state.builder.dataset = dataset;
      this.state.builder.columns = data.columns;
    });
  }

  paintBuilder() {
    const builder = this.state.builder;
    const numeric = builder.columns.filter((column) => column.semantic_role === "Measure" || column.is_numeric);
    const categories = builder.columns.filter((column) => ["Dimension", "Date Dimension"].includes(column.semantic_role) || column.is_category || column.is_date);
    const title = builder.dashboard ? builder.dashboard.dashboard_name : "Nouveau dashboard";
    this.$content.html(`
      ${this.header("Dashboard Builder", "L'utilisateur choisit quoi afficher, le système calcule techniquement.", `
        <button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>
        <button class="btn btn-default" data-action="builder-preview">${__("Preview")}</button>
        <button class="btn btn-primary" data-action="builder-save">${__("Save")}</button>
      `)}
      <div class="bi-builder">
        <div class="bi-card bi-canvas">
          <div class="bi-toolbar">
            <input class="bi-input" id="bi-builder-title" style="max-width: 360px;" value="${this.escapeAttr(title)}" placeholder="${__("Nom du dashboard")}">
            <select class="bi-select" id="bi-builder-dataset" style="max-width: 280px;">
              ${this.state.datasets.map((dataset) => `<option value="${dataset.name}" ${dataset.name === builder.dataset ? "selected" : ""}>${this.escape(dataset.dataset_name)}</option>`).join("")}
            </select>
            <button class="btn btn-default" data-action="builder-add">${this.icon("plus")} ${__("Add Widget")}</button>
          </div>
          <div class="bi-widget-list">
            ${builder.widgets.map((widget, index) => `
              <button class="bi-widget-tile ${this.state.selectedWidget === index ? "is-active" : ""}" data-action="builder-select" data-name="${index}">
                <strong>${this.escape(widget.title || widget.widget_name || "Widget")}</strong>
                <div class="bi-muted">${this.escape(widget.widget_type)} ${widget.chart_type ? ` / ${this.escape(widget.chart_type)}` : ""}</div>
              </button>
            `).join("") || `<div class="bi-empty">Ajoutez un widget pour commencer.</div>`}
          </div>
        </div>
        <aside class="bi-card">
          <div class="bi-card-header"><h3 class="bi-card-title">${__("Configuration")}</h3></div>
          ${this.renderWidgetConfig(numeric, categories)}
        </aside>
      </div>
    `);
  }

  renderWidgetConfig(numeric, categories) {
    const index = this.state.selectedWidget;
    const widget = Number.isInteger(index) ? this.state.builder.widgets[index] : null;
    if (!widget) return `<div class="bi-empty">Sélectionnez un widget.</div>`;
    return `
      <div class="bi-form-row"><label>${__("Nom du graphique")}</label><input class="bi-input" data-builder-field="title" value="${this.escapeAttr(widget.title || "")}"></div>
      <div class="bi-form-row"><label>${__("Type de widget")}</label>${this.select("widget_type", ["KPI", "Chart", "Table", "Text"], widget.widget_type)}</div>
      <div class="bi-form-row"><label>${__("Type de graphique")}</label>${this.select("chart_type", ["Bar", "Line", "Pie", "Donut", "Histogram", "Gauge", "Combined", "Scatter Plot", "Table", "KPI Card"], widget.chart_type)}</div>
      <div class="bi-form-row"><label>${__("Valeur à analyser")}</label>${this.columnSelect("value_field", numeric, widget.value_field)}</div>
      <div class="bi-form-row"><label>${__("Catégorie ou période")}</label>${this.columnSelect("category_field", categories, widget.category_field)}</div>
      <div class="bi-form-row"><label>${__("Type de calcul")}</label>${this.select("calculation_type", ["Total", "Moyenne", "Minimum", "Maximum", "Nombre", "Nombre unique"], widget.calculation_type || "Total")}</div>
      <div class="bi-form-row"><label>${__("Top N")}</label><input class="bi-input" type="number" data-builder-field="top_n" value="${this.escapeAttr(widget.top_n || 10)}"></div>
      <button class="btn btn-danger btn-sm" data-action="builder-delete">${__("Supprimer le widget")}</button>
    `;
  }

  select(field, options, value) {
    return `<select class="bi-select" data-builder-field="${field}">${options.map((option) => `<option value="${option}" ${option === value ? "selected" : ""}>${this.escape(option)}</option>`).join("")}</select>`;
  }

  columnSelect(field, columns, value) {
    return `<select class="bi-select" data-builder-field="${field}"><option value=""></option>${columns.map((column) => `<option value="${column.column_name}" ${column.column_name === value ? "selected" : ""}>${this.escape(column.column_label)}</option>`).join("")}</select>`;
  }

  renderAIAnalyses() {
    this.skeleton("AI Analyses");
    this.call("bi_studio.api.ai.get_ai_analyses").then((rows) => {
      this.$content.html(`
        ${this.header("AI Analyses", "Analyses créées uniquement depuis les dashboards.", `<button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>`)}
        <div class="bi-card">${this.renderAIAnalysesTable(rows)}</div>
      `);
    });
  }

  renderAIAnalysesTable(rows) {
    if (!rows.length) return `<div class="bi-empty">Aucune analyse IA.</div>`;
    return `
      <div class="bi-table-wrap"><table class="bi-table">
        <thead><tr><th>${__("Nom")}</th><th>${__("Dashboard associé")}</th><th>${__("Dataset source")}</th><th>${__("Propriétaire")}</th><th>${__("Date")}</th><th></th></tr></thead>
        <tbody>${rows.map((row) => `
          <tr>
            <td>${this.favoriteButton("BI AI Analysis", row.name, row.is_favorite)} ${this.escape(row.analysis_name)}</td>
            <td>${this.escape(row.dashboard_name || row.dashboard)}</td>
            <td>${this.escape(row.dataset_name || row.dataset)}</td>
            <td>${this.escape(row.owner)}</td>
            <td>${this.formatDate(row.generated_at)}</td>
            <td class="text-right">
              <button class="btn btn-xs btn-default" data-route="ai-analysis/${row.name}">${__("Consulter")}</button>
              <button class="btn btn-xs btn-default" data-action="rename-ai" data-name="${row.name}">${__("Renommer")}</button>
              <button class="btn btn-xs btn-danger" data-action="delete-ai" data-name="${row.name}">${__("Supprimer")}</button>
            </td>
          </tr>
        `).join("")}</tbody>
      </table></div>
    `;
  }

  renderAIAnalysisDetail(name) {
    this.skeleton("Analyse IA");
    this.call("bi_studio.api.ai.get_ai_analysis_detail", { analysis_name: name }).then((data) => {
      const analysis = data.analysis;
      this.$content.html(`
        ${this.header(analysis.analysis_name, `${data.dashboard_name} / ${data.dataset_name}`, `
          ${this.favoriteButton("BI AI Analysis", analysis.name, data.is_favorite)}
          <button class="btn btn-default" data-action="rename-ai" data-name="${analysis.name}">${__("Renommer")}</button>
          <button class="btn btn-danger" data-action="delete-ai" data-name="${analysis.name}">${__("Supprimer")}</button>
        `)}
        <div class="bi-grid">
          ${this.analysisBlock("Résumé court", analysis.summary_short)}
          ${this.analysisBlock("Niveau de confiance", analysis.confidence_level)}
          ${this.analysisBlock("Résumé détaillé", analysis.summary_detailed)}
          ${this.analysisBlock("Interprétation des KPI", analysis.kpi_interpretation)}
          ${this.analysisBlock("Interprétation des graphiques", analysis.chart_interpretation)}
          ${this.analysisBlock("Tendances", analysis.trends)}
          ${this.analysisBlock("Anomalies", analysis.anomalies)}
          ${this.analysisBlock("Recommandations", analysis.recommendations)}
          ${this.analysisBlock("KPI suggérés", analysis.suggested_kpis)}
          ${this.analysisBlock("Graphiques suggérés", analysis.suggested_charts)}
          ${this.analysisBlock("Conclusion", analysis.conclusion)}
        </div>
      `);
    });
  }

  analysisBlock(title, value) {
    return `<div class="bi-card"><h3 class="bi-card-title">${this.escape(title)}</h3><p>${this.escape(value || "Non renseigné")}</p></div>`;
  }

  renderFavorites() {
    this.skeleton("Favorites");
    this.call("bi_studio.api.favorites.get_favorites").then((rows) => {
      this.state.favorites = rows;
      this.$content.html(`
        ${this.header("Favorites", "Datasets, tableaux de bord et analyses IA enregistrés.", `<button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>`)}
        <div class="bi-card">
          ${this.listToolbar("favorites", __("Rechercher un favori"))}
          <div data-list-results="favorites">${this.renderFavoritesTable(this.filteredRows("favorites", rows))}</div>
        </div>
      `);
    });
  }

  renderFavoritesTable(rows) {
    if (!rows.length) return `<div class="bi-empty">Aucun favori.</div>`;
    return `
      <div class="bi-table-wrap"><table class="bi-table">
        <thead><tr><th>${__("Type d'élément")}</th><th>${__("Nom de l'élément")}</th><th>${__("Propriétaire")}</th><th>${__("Taille")}</th><th>${__("Date d'ajout")}</th><th></th></tr></thead>
        <tbody>${rows.map((row) => `
          <tr>
            <td>${this.escape(row.reference_doctype)}</td>
            <td>${this.escape(row.label || row.reference_name)}</td>
            <td>${this.escape(row.reference_owner || row.owner || "")}</td>
            <td>${this.escape(row.size_label || "")}</td>
            <td>${this.formatDate(row.favorited_at)}</td>
            <td class="text-right">
              <button class="btn btn-xs btn-default" data-route="${row.route.replace("/desk/bi_studio/", "")}">${__("Consulter")}</button>
              ${this.favoriteButton(row.reference_doctype, row.reference_name, true)}
            </td>
          </tr>
        `).join("")}</tbody>
      </table></div>
    `;
  }

  renderAdminRoute(section) {
    if (!section) return this.renderAdminDashboard();
    if (section === "datasets") return this.renderAdminTable("All Datasets", "bi_studio.api.admin.get_all_datasets", "dataset_name", "admin-delete-dataset");
    if (section === "dashboards") return this.renderAdminTable("All Dashboards", "bi_studio.api.admin.get_all_dashboards", "dashboard_name", "admin-delete-dashboard");
    if (section === "ai-analyses") return this.renderAdminTable("All AI Analyses", "bi_studio.api.admin.get_all_ai_analyses", "analysis_name", "admin-delete-ai");
    if (section === "import-jobs") return this.renderAdminTable("Import Jobs", "bi_studio.api.admin.get_recent_imports", "name", null);
    return this.renderAdminDashboard();
  }

  renderAdminDashboard() {
    this.skeleton("Admin Dashboard");
    Promise.all([
      this.call("bi_studio.api.admin.get_admin_dashboard_summary"),
      this.call("bi_studio.api.admin.get_imports_over_time", { group_by: "day" }),
    ]).then(([summary, imports]) => {
      this.$content.html(`
        ${this.header("Admin Dashboard", "Vue globale simple de BI Studio.", `<button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>`)}
        <div class="bi-kpi-grid">
          ${this.kpiCard(summary.total_datasets, "Nombre total de datasets")}
          ${this.kpiCard(summary.total_dashboards, "Nombre total de dashboards")}
          ${this.kpiCard(summary.total_ai_analyses, "Nombre total d'analyses IA")}
          ${this.kpiCard(summary.top_ai_user.total_ai_analyses || 0, "Top utilisateur analyses IA")}
          ${this.kpiCard(summary.imports_over_time_summary.imports_this_month, "Imports ce mois")}
        </div>
        <div class="bi-chart-grid">
          ${this.chartShell("admin-imports", "Importations en fonction du temps")}
          ${this.chartShell("admin-usage", "Taux d'exploitation des datasets")}
        </div>
      `);
      setTimeout(() => {
        this.drawChart("admin-imports", "Line", { labels: imports.map((row) => row.period), values: imports.map((row) => row.imports) });
        this.drawChart("admin-usage", "Donut", { labels: ["Datasets exploités", "Datasets non exploités"], values: [summary.dataset_usage_rate.used, summary.dataset_usage_rate.unused] });
      }, 50);
    });
  }

  renderAdminTable(title, method, labelField, deleteAction) {
    this.skeleton(title);
    this.call(method).then((rows) => {
      const columns = rows[0] ? Object.keys(rows[0]) : [];
      this.$content.html(`
        ${this.header(title, "Administration BI Studio", `<button class="btn btn-default" data-action="refresh">${this.icon("refresh-cw")} ${__("Refresh")}</button>`)}
        <div class="bi-card">
          ${rows.length ? `<div class="bi-table-wrap"><table class="bi-table">
            <thead><tr>${columns.map((column) => `<th>${this.escape(column)}</th>`).join("")}<th></th></tr></thead>
            <tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${this.escape(row[column])}</td>`).join("")}<td class="text-right">${deleteAction ? `<button class="btn btn-xs btn-danger" data-action="${deleteAction}" data-name="${row.name}">${__("Supprimer")}</button>` : ""}</td></tr>`).join("")}</tbody>
          </table></div>` : `<div class="bi-empty">Aucun élément.</div>`}
        </div>
      `);
    });
  }

  handleAction(action, name, type, $target) {
    if (action === "toggle-sidebar") {
      this.state.collapsed = !this.state.collapsed;
      return this.renderSidebar();
    }
    if (action === "search") return this.openSearch();
    if (action === "refresh") return this.show();
    if (action === "open-ai-pipeline") return frappe.set_route("bi_intelligent_pipeline");
    if (action === "favorite") return this.toggleFavorite(type, name);
    if (action === "rename-dataset") return this.renameItem("Dataset", name, "bi_studio.api.dataset.rename_dataset", "new_name", "dataset_name");
    if (action === "rename-dashboard") return this.renameItem("Dashboard", name, "bi_studio.api.dashboard.rename_dashboard", "new_name", "dashboard_name");
    if (action === "rename-ai") return this.renameItem("Analyse IA", name, "bi_studio.api.ai.rename_ai_analysis", "new_name", "analysis_name");
    if (action === "delete-dataset") return this.confirmDelete("ce dataset", "bi_studio.api.dataset.delete_dataset", { dataset_name: name });
    if (action === "delete-dashboard") return this.confirmDelete("ce dashboard", "bi_studio.api.dashboard.delete_dashboard", { dashboard_name: name });
    if (action === "delete-ai") return this.confirmDelete("cette analyse IA", "bi_studio.api.ai.delete_ai_analysis", { analysis_name: name });
    if (action === "admin-delete-dataset") return this.confirmDelete("ce dataset", "bi_studio.api.admin.admin_delete_dataset", { dataset_name: name });
    if (action === "admin-delete-dashboard") return this.confirmDelete("ce dashboard", "bi_studio.api.admin.admin_delete_dashboard", { dashboard_name: name });
    if (action === "admin-delete-ai") return this.confirmDelete("cette analyse IA", "bi_studio.api.admin.admin_delete_ai_analysis", { analysis_name: name });
    if (action === "export-dataset") return this.exportDataset(name);
    if (action === "export-dashboard") return this.exportDashboard(name);
    if (action === "export-chart") return this.exportChart(name);
    if (action === "dataset-tab") {
      this.state.activeTab = name;
      return this.renderDatasetDetail(this.currentDatasetDetail.dataset.name);
    }
    if (action === "builder-add") return this.builderAddWidget();
    if (action === "builder-select") {
      this.state.selectedWidget = Number(name);
      return this.paintBuilder();
    }
    if (action === "builder-delete") return this.builderDeleteWidget();
    if (action === "builder-save") return this.builderSave();
    if (action === "builder-preview") return this.builderPreview();
  }

  renameItem(label, name, method, argName) {
    frappe.prompt({ label: __("Nouveau nom"), fieldname: "new_name", fieldtype: "Data", reqd: 1 }, (values) => {
      const args = { [argName]: values.new_name };
      if (label === "Dataset") args.dataset_name = name;
      if (label === "Dashboard") args.dashboard_name = name;
      if (label === "Analyse IA") args.analysis_name = name;
      this.call(method, args).then(() => this.show());
    }, __("Renommer " + label));
  }

  confirmDelete(label, method, args) {
    frappe.confirm(__("Supprimer {0} ?", [label]), () => {
      this.call(method, args).then(() => {
        frappe.show_alert({ message: __("Suppression effectuée."), indicator: "green" });
        this.navigate("");
      });
    });
  }

  exportDataset(name) {
    this.call("bi_studio.api.dataset.export_clean_dataset", { dataset_name: name }).then((file) => {
      this.downloadFile(file);
    });
  }

  exportDashboard(name) {
    this.captureDashboardPng().then((imageData) => {
      this.call("bi_studio.api.dashboard.export_dashboard_png", { dashboard_name: name, image_data: imageData }).then((file) => {
        this.downloadFile(file);
      });
    });
  }

  downloadFile(file) {
    if (!file || !file.file_url) return;
    const link = document.createElement("a");
    link.href = file.file_url;
    link.download = file.file_name || "";
    link.target = "_self";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  exportChart(id) {
    const canvas = document.getElementById(`chart-${id}`);
    if (!canvas) return;
    const link = document.createElement("a");
    link.download = `${id}.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
  }

  captureDashboardPng() {
    if (window.html2canvas) {
      return window.html2canvas(document.querySelector(".bi-dashboard-export-area")).then((canvas) => canvas.toDataURL("image/png"));
    }
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js";
      script.onload = () => window.html2canvas(document.querySelector(".bi-dashboard-export-area")).then((canvas) => resolve(canvas.toDataURL("image/png")));
      script.onerror = () => {
        const canvas = document.querySelector(".bi-chart-canvas");
        resolve(canvas ? canvas.toDataURL("image/png") : "");
      };
      document.head.appendChild(script);
    });
  }

  toggleFavorite(referenceDoctype, referenceName) {
    this.call("bi_studio.api.favorites.toggle_favorite", {
      reference_doctype: referenceDoctype,
      reference_name: referenceName,
    }).then(() => this.show());
  }

  favoriteButton(referenceDoctype, referenceName, active) {
    return `<button class="btn btn-xs btn-default ${active ? "bi-favorite" : ""}" data-action="favorite" data-type="${referenceDoctype}" data-name="${referenceName}" title="${__("Favoris")}">${this.icon("star")}</button>`;
  }

  builderAddWidget() {
    const columns = this.state.builder.columns;
    const numeric = columns.find((column) => column.semantic_role === "Measure" || column.is_numeric);
    const category = columns.find((column) => ["Dimension", "Date Dimension"].includes(column.semantic_role) || column.is_category || column.is_date);
    this.state.builder.widgets.push({
      id: `widget_${Date.now()}`,
      widget_type: "Chart",
      title: "Nouveau graphique",
      chart_type: "Bar",
      value_field: numeric && numeric.column_name,
      category_field: category && category.column_name,
      calculation_type: "Total",
      top_n: 10,
      visible: true,
    });
    this.state.selectedWidget = this.state.builder.widgets.length - 1;
    this.paintBuilder();
  }

  builderDeleteWidget() {
    if (!Number.isInteger(this.state.selectedWidget)) return;
    this.state.builder.widgets.splice(this.state.selectedWidget, 1);
    this.state.selectedWidget = null;
    this.paintBuilder();
  }

  builderSave() {
    this.syncBuilderForm();
    const builder = this.state.builder;
    if (!builder.dataset) {
      frappe.msgprint(__("Choisissez un dataset."));
      return;
    }
    const dashboardTitle = $("#bi-builder-title").val();
    const args = {
      dashboard_title: dashboardTitle,
      dashboard_name: dashboardTitle,
      dataset_name: builder.dataset,
      widgets_json: JSON.stringify(builder.widgets),
      layout_json: JSON.stringify({ grid: builder.widgets.map((widget) => ({ id: widget.id, width: 6, height: 4 })) }),
      filters_json: "{}",
    };
    const method = builder.dashboard ? "bi_studio.api.dashboard.update_dashboard" : "bi_studio.api.dashboard.create_dashboard";
    if (builder.dashboard) args.dashboard_name = builder.dashboard.name;
    this.call(method, args).then((result) => {
      frappe.show_alert({ message: __("Dashboard sauvegardé."), indicator: "green" });
      this.navigate(`dashboard/${result.dashboard}`);
    });
  }

  builderPreview() {
    this.builderSave();
  }

  syncBuilderForm() {
    const builder = this.state.builder;
    const selectedDataset = $("#bi-builder-dataset").val();
    if (selectedDataset && selectedDataset !== builder.dataset) {
      builder.dataset = selectedDataset;
    }
    if (Number.isInteger(this.state.selectedWidget)) {
      const widget = builder.widgets[this.state.selectedWidget];
      $("[data-builder-field]").each((_, input) => {
        const $input = $(input);
        widget[$input.attr("data-builder-field")] = $input.val();
      });
      widget.top_n = Number(widget.top_n || 10);
    }
  }

  openSearch() {
    frappe.prompt({ fieldname: "query", fieldtype: "Data", label: __("Recherche"), reqd: 1 }, (values) => {
      this.navigate("datasets");
      this.state.listFilters.datasets.search = values.query || "";
      setTimeout(() => {
        this.$content
          .find("[data-list-filter][data-list='datasets'][data-field='search']")
          .val(values.query)
          .trigger("input");
      }, 120);
    }, __("Recherche BI Studio"));
  }

  badge(value) {
    const color = value === "Ready" || value === "Success" ? "green" : value === "Failed" ? "red" : "blue";
    return `<span class="indicator-pill ${color}">${this.escape(value || "")}</span>`;
  }

  formatNumber(value) {
    const number = Number(value || 0);
    return new Intl.NumberFormat(frappe.boot.lang || "fr-FR", { maximumFractionDigits: 2 }).format(number);
  }

  formatPercent(value) {
    const number = Number(value || 0) * 100;
    return `${new Intl.NumberFormat(frappe.boot.lang || "fr-FR", { maximumFractionDigits: 1 }).format(number)}%`;
  }

  formatDate(value) {
    return value ? frappe.datetime.str_to_user(value) : "";
  }

  escape(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }

  escapeAttr(value) {
    return this.escape(value).replace(/`/g, "&#96;");
  }
}
