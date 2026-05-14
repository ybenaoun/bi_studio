(function (root) {
  const VUE_URL = "https://cdn.jsdelivr.net/npm/vue@3.5.13/dist/vue.global.prod.js";
  const ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js";
  const STYLE_URL = "/assets/bi_studio/css/dashboard_v1_renderer.css";
  const utils = root.BIStudioDashboardV1Utils;

  if (!utils) {
    throw new Error("BIStudioDashboardV1Utils must be loaded before dashboard_v1_renderer.js");
  }

  function loadScriptOnce(url, globalName) {
    if (globalName && root[globalName]) return Promise.resolve(root[globalName]);
    const key = `__biStudioScript_${url}`;
    if (root[key]) return root[key];
    root[key] = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = url;
      script.async = true;
      script.onload = () => resolve(globalName ? root[globalName] : true);
      script.onerror = () => reject(new Error(`Unable to load ${url}`));
      document.head.appendChild(script);
    });
    return root[key];
  }

  function loadStyleOnce(url) {
    if (document.querySelector(`link[data-bi-dashboard-v1-style="${url}"]`)) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    link.setAttribute("data-bi-dashboard-v1-style", url);
    document.head.appendChild(link);
  }

  async function ensureDependencies() {
    loadStyleOnce(STYLE_URL);
    await loadScriptOnce(VUE_URL, "Vue");
    await loadScriptOnce(ECHARTS_URL, "echarts");
  }

  function componentDefinitions() {
    const widgetRegistry = {};

    const ErrorPanel = {
      name: "ErrorPanel",
      props: { title: String, message: String },
      template: `
        <div class="bi-v1-error-panel">
          <strong>{{ title || "Erreur" }}</strong>
          <span>{{ message || "Une erreur contrôlée empêche le rendu." }}</span>
        </div>
      `,
    };

    const EmptyState = {
      name: "EmptyState",
      props: { title: String, message: String },
      template: `
        <div class="bi-v1-empty-state">
          <strong>{{ title || "Aucune donnée" }}</strong>
          <span>{{ message || "Aucun élément disponible pour ce tableau de bord." }}</span>
        </div>
      `,
    };

    const LoadingSkeleton = {
      name: "LoadingSkeleton",
      template: `
        <div class="bi-v1-skeleton">
          <div></div><div></div><div></div>
        </div>
      `,
    };

    const DashboardHeader = {
      name: "DashboardHeader",
      props: {
        dashboard: { type: Object, default: () => ({}) },
        qualityScore: [Number, String],
        status: String,
        actions: { type: Array, default: () => [] },
      },
      emits: ["action"],
      computed: {
        title() {
          return this.dashboard.title || "Dashboard";
        },
        description() {
          return this.dashboard.description || "";
        },
        hasQuality() {
          return this.qualityScore !== null && this.qualityScore !== undefined && this.qualityScore !== "";
        },
        qualityClass() {
          const score = Number(this.qualityScore || 0);
          if (score < 60) return "is-bad";
          if (score < 85) return "is-warn";
          return "is-good";
        },
        qualityLabel() {
          return `${utils.formatValue(Number(this.qualityScore || 0), { type: "number", decimals: 0 })}/100`;
        },
      },
      methods: {
        actionClass(action) {
          return action.variant === "primary" ? "btn btn-primary" : "btn btn-default";
        },
      },
      template: `
        <header class="bi-v1-dashboard-header">
          <div class="bi-v1-dashboard-title-block">
            <div class="bi-v1-eyebrow">dashboard.v1</div>
            <h1>{{ title }}</h1>
            <p v-if="description">{{ description }}</p>
            <div class="bi-v1-badges">
              <span v-if="hasQuality" class="bi-v1-badge" :class="qualityClass">Qualité {{ qualityLabel }}</span>
              <span v-if="status" class="bi-v1-badge is-neutral">{{ status }}</span>
            </div>
          </div>
          <div v-if="actions.length" class="bi-v1-header-actions">
            <button
              v-for="action in actions"
              :key="action.key"
              :class="actionClass(action)"
              type="button"
              :disabled="action.disabled"
              @click="$emit('action', action.key)"
            >{{ action.label }}</button>
          </div>
        </header>
      `,
    };

    const DashboardFilterBar = {
      name: "DashboardFilterBar",
      props: {
        filters: { type: Array, default: () => [] },
        modelValue: { type: Object, default: () => ({}) },
        loading: Boolean,
      },
      emits: ["update:modelValue", "change", "reset"],
      methods: {
        filterType(filter) {
          return filter.type || "select";
        },
        filterValue(filter) {
          return this.modelValue[filter.id] ?? filter.value ?? (this.filterType(filter) === "multi_select" ? [] : "");
        },
        rangeValue(filter, edge) {
          const value = this.filterValue(filter);
          return value && typeof value === "object" ? value[edge] || "" : "";
        },
        filterOptions(filter) {
          return (filter.options || []).map((option) => {
            if (option && typeof option === "object") {
              return { label: option.label ?? option.value, value: option.value ?? option.label };
            }
            return { label: option, value: option };
          });
        },
        updateFilter(filter, value) {
          const next = { ...this.modelValue, [filter.id]: value };
          this.$emit("update:modelValue", next);
          this.$emit("change", { filter, values: next });
        },
        updateRange(filter, edge, value) {
          const current = this.filterValue(filter);
          this.updateFilter(filter, { ...(current && typeof current === "object" ? current : {}), [edge]: value });
        },
        updateMulti(filter, event) {
          const values = Array.from(event.target.selectedOptions).map((option) => option.value);
          this.updateFilter(filter, values);
        },
        reset() {
          this.$emit("update:modelValue", {});
          this.$emit("reset");
          this.$emit("change", { filter: null, values: {} });
        },
      },
      template: `
        <section class="bi-v1-filter-bar">
          <div class="bi-v1-filter-list">
            <label v-for="filter in filters" :key="filter.id" class="bi-v1-filter-control">
              <span>{{ filter.label || filter.field }}</span>

              <select
                v-if="filterType(filter) === 'select'"
                class="bi-v1-input"
                :value="filterValue(filter)"
                :disabled="loading || filter.disabled"
                @change="updateFilter(filter, $event.target.value)"
              >
                <option value="">Tous</option>
                <option v-for="option in filterOptions(filter)" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>

              <select
                v-else-if="filterType(filter) === 'multi_select'"
                class="bi-v1-input"
                multiple
                :value="filterValue(filter)"
                :disabled="loading || filter.disabled"
                @change="updateMulti(filter, $event)"
              >
                <option v-for="option in filterOptions(filter)" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>

              <div v-else-if="filterType(filter) === 'date_range'" class="bi-v1-range">
                <input class="bi-v1-input" type="date" :value="rangeValue(filter, 'from')" :disabled="loading || filter.disabled" @change="updateRange(filter, 'from', $event.target.value)">
                <input class="bi-v1-input" type="date" :value="rangeValue(filter, 'to')" :disabled="loading || filter.disabled" @change="updateRange(filter, 'to', $event.target.value)">
              </div>

              <div v-else-if="filterType(filter) === 'number_range'" class="bi-v1-range">
                <input class="bi-v1-input" type="number" :value="rangeValue(filter, 'min')" :disabled="loading || filter.disabled" @input="updateRange(filter, 'min', $event.target.value)">
                <input class="bi-v1-input" type="number" :value="rangeValue(filter, 'max')" :disabled="loading || filter.disabled" @input="updateRange(filter, 'max', $event.target.value)">
              </div>

              <input
                v-else
                class="bi-v1-input"
                type="search"
                :value="filterValue(filter)"
                :disabled="loading || filter.disabled"
                @input="updateFilter(filter, $event.target.value)"
              >
            </label>
          </div>
          <button class="btn btn-default" type="button" :disabled="loading" @click="reset">Réinitialiser</button>
        </section>
      `,
    };

    const KpiCard = {
      name: "KpiCard",
      props: {
        widget: { type: Object, required: true },
        data: { type: Object, default: () => ({}) },
      },
      computed: {
        display() {
          return utils.getWidgetDisplay(this.widget, this.data);
        },
        value() {
          return utils.formatValue(this.data.value, this.display);
        },
      },
      template: `
        <section class="bi-v1-widget-card bi-v1-kpi-card">
          <div class="bi-v1-widget-heading">
            <h2>{{ widget.title }}</h2>
            <p v-if="widget.description">{{ widget.description }}</p>
          </div>
          <div v-if="data.error" class="bi-v1-inline-error">{{ data.error }}</div>
          <div v-else class="bi-v1-kpi-value">{{ value }}</div>
        </section>
      `,
    };

    const EChartCard = {
      name: "EChartCard",
      props: {
        widget: { type: Object, required: true },
        data: { type: Object, default: () => ({}) },
      },
        data() {
          return {
            chart: null,
            resizeObserver: null,
            resizeHandler: null,
          };
        },
      computed: {
        normalizedData() {
          return utils.normalizeChartData(this.data);
        },
        isEmpty() {
          return !this.normalizedData.series.length || this.normalizedData.values.every((value) => Number(value) === 0);
        },
      },
      watch: {
        data: {
          deep: true,
          handler() {
            this.renderChart();
          },
        },
        widget: {
          deep: true,
          handler() {
            this.renderChart();
          },
        },
      },
      mounted() {
        this.$nextTick(() => {
          this.ensureChart();
          this.renderChart();
          this.observeResize();
        });
      },
        beforeUnmount() {
          if (this.resizeObserver) this.resizeObserver.disconnect();
          if (this.resizeHandler) root.removeEventListener("resize", this.resizeHandler);
          if (this.chart) this.chart.dispose();
          this.chart = null;
        },
      methods: {
        ensureChart() {
          if (this.chart || !this.$refs.chartEl || !root.echarts || this.data.error || this.isEmpty) return;
          this.chart = root.echarts.init(this.$refs.chartEl, null, { renderer: "canvas" });
        },
        renderChart() {
          this.$nextTick(() => {
            if (this.data.error || this.isEmpty) {
              if (this.chart) {
                this.chart.dispose();
                this.chart = null;
              }
              return;
            }
            this.ensureChart();
            if (!this.chart) return;
            this.chart.setOption(utils.buildChartOptions(this.widget, this.data), true);
            this.chart.resize();
          });
        },
        observeResize() {
          if (!this.$refs.chartEl) return;
          if ("ResizeObserver" in root) {
            this.resizeObserver = new ResizeObserver(() => {
              if (this.chart) this.chart.resize();
            });
            this.resizeObserver.observe(this.$refs.chartEl);
            return;
          }
            this.resizeHandler = () => {
              if (this.chart) this.chart.resize();
            };
            root.addEventListener("resize", this.resizeHandler);
          },
        },
      template: `
        <section class="bi-v1-widget-card bi-v1-chart-card">
          <div class="bi-v1-widget-heading">
            <h2>{{ widget.title }}</h2>
            <p v-if="widget.description">{{ widget.description }}</p>
          </div>
          <ErrorPanel v-if="data.error" title="Données indisponibles" :message="data.error" />
          <EmptyState v-else-if="isEmpty" title="Aucune donnée" message="Ce graphique ne contient aucun point à afficher." />
          <div v-show="!data.error && !isEmpty" ref="chartEl" class="bi-v1-chart-canvas"></div>
        </section>
      `,
      components: { ErrorPanel, EmptyState },
    };

    const DataTableCard = {
      name: "DataTableCard",
      props: {
        widget: { type: Object, required: true },
        data: { type: Object, default: () => ({}) },
      },
      data() {
        return { search: "" };
      },
      computed: {
        columns() {
          return this.data.columns || [];
        },
        rows() {
          return this.data.rows || [];
        },
        filteredRows() {
          const query = this.search.trim().toLowerCase();
          if (!query) return this.rows;
          return this.rows.filter((row) => this.columns.some((column) => String(row[column] ?? "").toLowerCase().includes(query)));
        },
      },
      methods: {
        isNumberColumn(column) {
          return this.rows.some((row) => typeof row[column] === "number");
        },
        cellValue(value) {
          if (typeof value === "number") {
            return utils.formatValue(value, { type: "number", decimals: Number.isInteger(value) ? 0 : 2 });
          }
          return utils.formatValue(value, { type: "text" });
        },
      },
      template: `
        <section class="bi-v1-widget-card bi-v1-table-card">
          <div class="bi-v1-table-header">
            <div class="bi-v1-widget-heading">
              <h2>{{ widget.title }}</h2>
              <p v-if="widget.description">{{ widget.description }}</p>
            </div>
            <input v-if="rows.length" v-model="search" class="bi-v1-input bi-v1-table-search" type="search" placeholder="Rechercher">
          </div>
          <ErrorPanel v-if="data.error" title="Données indisponibles" :message="data.error" />
          <EmptyState v-else-if="!rows.length" title="Aucune ligne" message="La table ne contient aucune donnée à afficher." />
          <div v-else class="bi-v1-table-wrap">
            <table class="bi-v1-table">
              <thead>
                <tr>
                  <th v-for="column in columns" :key="column" :class="{ 'is-number': isNumberColumn(column) }">{{ column }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, index) in filteredRows.slice(0, 100)" :key="index">
                  <td v-for="column in columns" :key="column" :class="{ 'is-number': typeof row[column] === 'number' }">{{ cellValue(row[column]) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      `,
      components: { ErrorPanel, EmptyState },
    };

    const UnsupportedWidget = {
      name: "UnsupportedWidget",
      props: { widget: { type: Object, required: true } },
      computed: {
        type() {
          return this.widget.widget_type || this.widget.type || "inconnu";
        },
      },
      template: `
        <section class="bi-v1-widget-card">
          <ErrorPanel title="Widget non supporté" :message="'Type de widget inconnu: ' + type" />
        </section>
      `,
      components: { ErrorPanel },
    };

    widgetRegistry.kpi = KpiCard;
    widgetRegistry.bar_chart = EChartCard;
    widgetRegistry.line_chart = EChartCard;
    widgetRegistry.pie_chart = EChartCard;
    widgetRegistry.donut_chart = EChartCard;
    widgetRegistry.table = DataTableCard;

    const DashboardGrid = {
      name: "DashboardGrid",
      props: {
        widgets: { type: Array, default: () => [] },
        data: { type: Object, default: () => ({}) },
        layout: { type: Object, default: () => ({ columns: 12, row_height: 80 }) },
      },
      methods: {
        resolveWidget(widget) {
          const type = utils.normalizeWidgetType(widget);
          const component = widgetRegistry[type];
          if (!component && utils.isDevelopment()) {
            console.error("BI Studio dashboard.v1 widget_type unsupported", type, widget);
          }
          return component || UnsupportedWidget;
        },
        widgetData(widget) {
          return this.data[utils.widgetId(widget)] || {};
        },
        gridStyle(widget) {
          return utils.buildGridStyle(widget, this.layout);
        },
      },
      template: `
        <section class="bi-v1-dashboard-grid">
          <component
            v-for="widget in widgets"
            :key="widget.id"
            :is="resolveWidget(widget)"
            :widget="widget"
            :data="widgetData(widget)"
            :style="gridStyle(widget)"
          />
        </section>
      `,
    };

    const DashboardRenderer = {
      name: "DashboardRenderer",
      props: {
        payload: { type: Object, required: true },
        options: { type: Object, default: () => ({}) },
      },
      emits: ["action", "filters-change"],
      data() {
        return {
          filterValues: {},
          loading: false,
        };
      },
      computed: {
        normalized() {
          return utils.normalizeDashboardPayload(this.payload);
        },
        definition() {
          return this.normalized.definition || {};
        },
        dashboard() {
          return this.definition.dashboard || {};
        },
        layout() {
          return this.definition.layout || { columns: 12, row_height: 80 };
        },
        widgets() {
          return this.definition.widgets || [];
        },
        filters() {
          return this.definition.filters || [];
        },
        dataMap() {
          return this.normalized.data || {};
        },
        qualityScore() {
          return this.options.quality_score ?? (this.normalized.meta && this.normalized.meta.quality_score);
        },
        status() {
          return this.options.status || this.dashboard.status || "";
        },
        actions() {
          return this.options.actions || [];
        },
      },
      methods: {
        handleAction(key) {
          if (typeof this.options.onAction === "function") this.options.onAction(key, this.normalized);
          this.$emit("action", key);
        },
        handleFiltersChange(event) {
          if (typeof this.options.onFiltersChange === "function") this.options.onFiltersChange(event);
          this.$emit("filters-change", event);
        },
      },
      template: `
        <div class="bi-v1-dashboard bi-dashboard-export-area">
          <DashboardHeader
            :dashboard="dashboard"
            :quality-score="qualityScore"
            :status="status"
            :actions="actions"
            @action="handleAction"
          />
          <DashboardFilterBar
            v-if="filters.length"
            v-model="filterValues"
            :filters="filters"
            :loading="loading"
            @change="handleFiltersChange"
          />
          <EmptyState
            v-if="!widgets.length"
            title="Aucun widget"
            message="Le dashboard validé ne contient aucun widget à rendre."
          />
          <DashboardGrid v-else :widgets="widgets" :data="dataMap" :layout="layout" />
        </div>
      `,
      components: { DashboardHeader, DashboardFilterBar, DashboardGrid, EmptyState },
    };

    const Root = {
      name: "DashboardV1Root",
      components: { DashboardRenderer },
      data() {
        return {
          currentPayload: {},
          currentOptions: {},
        };
      },
      methods: {
        update(payload, options) {
          this.currentPayload = payload || {};
          this.currentOptions = options || this.currentOptions || {};
        },
      },
      template: `<DashboardRenderer :payload="currentPayload" :options="currentOptions" />`,
    };

    return { Root, widgetRegistry, buildChartOptions: utils.buildChartOptions };
  }

  async function mount(container, payload, options) {
    if (!container) throw new Error("Dashboard mount container is required");
    await ensureDependencies();
    const Vue = root.Vue;
    const { Root } = componentDefinitions();
    const app = Vue.createApp(Root);
    app.config.errorHandler = function (error) {
      console.error("BI Studio dashboard.v1 renderer error", error);
    };
    const vm = app.mount(container);
    vm.update(payload, options || {});
    return {
      app,
      vm,
      update(nextPayload, nextOptions) {
        vm.update(nextPayload, nextOptions);
      },
      unmount() {
        app.unmount();
        container.innerHTML = "";
      },
    };
  }

  root.BIStudioDashboardV1Renderer = {
    mount,
    ensureDependencies,
    buildChartOptions: utils.buildChartOptions,
    formatValue: utils.formatValue,
  };
})(window);
