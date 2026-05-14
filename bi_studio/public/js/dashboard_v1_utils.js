(function (root, factory) {
  const api = factory();
  root.BIStudioDashboardV1Utils = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis, function () {
  const V1_TO_RENDERER_TYPE = {
    kpi: "kpi",
    kpi_card: "kpi",
    bar: "bar_chart",
    bar_chart: "bar_chart",
    line: "line_chart",
    line_chart: "line_chart",
    pie: "pie_chart",
    pie_chart: "pie_chart",
    donut: "donut_chart",
    donut_chart: "donut_chart",
    table: "table",
    data_table: "table",
  };

  const CHART_TYPES = new Set(["bar_chart", "line_chart", "pie_chart", "donut_chart"]);

  function locale() {
    return (typeof frappe !== "undefined" && frappe.boot && frappe.boot.lang) || "fr-FR";
  }

  function toNumber(value) {
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (value === null || value === undefined || value === "") return null;
    const numeric = Number(String(value).replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(numeric) ? numeric : null;
  }

  function integerOr(value, fallback) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? Math.trunc(numeric) : fallback;
  }

  function getDisplayType(display) {
    const source = display || {};
    return source.type || (source.format && source.format.type) || "number";
  }

  function getDecimals(display, fallback) {
    const source = display || {};
    const candidate = source.decimals ?? (source.format && source.format.decimals);
    if (candidate === 0 || Number.isFinite(Number(candidate))) return Number(candidate);
    return fallback;
  }

  function formatValue(value, display) {
    if (value === null || value === undefined || value === "") return "—";

    const type = getDisplayType(display);
    if (type === "text") return String(value);

    if (type === "date") {
      if (typeof frappe !== "undefined" && frappe.datetime) {
        return frappe.datetime.str_to_user(String(value));
      }
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(locale()).format(date);
    }

    const number = toNumber(value);
    if (number === null) return String(value);

    if (type === "currency") {
      const decimals = getDecimals(display, 2);
      return new Intl.NumberFormat(locale(), {
        style: "currency",
        currency: (display && (display.currency || (display.format && display.format.currency))) || "EUR",
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(number);
    }

    if (type === "percentage") {
      const decimals = getDecimals(display, 1);
      return new Intl.NumberFormat(locale(), {
        style: "percent",
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(number);
    }

    const decimals = getDecimals(display, 0);
    return new Intl.NumberFormat(locale(), {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(number);
  }

  function formatCompactValue(value, display) {
    const number = toNumber(value);
    if (number === null) return "";
    const type = getDisplayType(display);
    if (type === "currency") {
      return new Intl.NumberFormat(locale(), {
        notation: "compact",
        maximumFractionDigits: 1,
        style: "currency",
        currency: (display && display.currency) || "EUR",
      }).format(number);
    }
    if (type === "percentage") return formatValue(number, display);
    return new Intl.NumberFormat(locale(), {
      notation: Math.abs(number) >= 10000 ? "compact" : "standard",
      maximumFractionDigits: Math.abs(number) >= 10000 ? 1 : getDecimals(display, 0),
    }).format(number);
  }

  function normalizeWidgetType(widget) {
    const config = (widget && widget.config) || {};
    const rawType = String(
      (widget && (widget.widget_type || widget.type)) ||
      config.type ||
      ""
    ).toLowerCase();
    return V1_TO_RENDERER_TYPE[rawType] || rawType;
  }

  function widgetId(widget) {
    return widget && (widget.id || widget.widget_id || widget.name);
  }

  function widgetPosition(widget) {
    const config = (widget && widget.config) || {};
    return (widget && widget.position) || config.position || {};
  }

  function getWidgetDisplay(widget, data) {
    const config = (widget && widget.config) || {};
    const options = config.options || widget.options || {};
    const format = (data && data.format) || config.format || widget.format || {};
    const display = widget.display || {};
    return {
      ...options,
      ...format,
      ...display,
      format,
    };
  }

  function normalizeFilter(row) {
    const config = row.config || row;
    const data = row.data || {};
    const id = widgetId(row);
    const field = data.field || (config.data && config.data.metric) || config.metric || id;
    return {
      id,
      type: row.filter_type || config.filter_type || "select",
      label: row.title || config.title || field,
      field,
      options: data.values || config.options || [],
      value: row.value ?? config.value ?? "",
      disabled: Boolean(row.disabled),
    };
  }

  function normalizeWidget(row) {
    const config = row.config || row;
    const id = widgetId(row);
    const type = normalizeWidgetType(row);
    return {
      id,
      widget_type: type,
      title: row.title || config.title || id || "Widget",
      description: row.description || config.description || "",
      position: widgetPosition(row),
      display: getWidgetDisplay(row, row.data),
      config,
    };
  }

  function normalizeDashboardPayload(payload) {
    const source = payload || {};
    if (source.definition && source.data) {
      const definition = {
        ...source.definition,
        dashboard: source.definition.dashboard || {
          title: source.definition.title || "Dashboard",
          description: source.definition.description || "",
        },
        widgets: (source.definition.widgets || []).map((widget) => normalizeWidget(widget)),
        filters: source.definition.filters || [],
      };
      return { definition, data: source.data || {} };
    }

    const widgets = [];
    const filters = [];
    const data = {};

    (source.widgets || []).forEach((row) => {
      const type = normalizeWidgetType(row);
      const id = widgetId(row);
      if (!id) return;
      data[id] = row.data || {};
      if (type === "filter" || row.widget_type === "filter" || (row.config && row.config.type === "filter")) {
        filters.push(normalizeFilter(row));
        return;
      }
      widgets.push(normalizeWidget(row));
    });

    (source.filters || []).forEach((filter) => {
      const normalized = normalizeFilter(filter);
      if (!filters.some((item) => item.id === normalized.id)) filters.push(normalized);
    });

    widgets.sort((left, right) => {
      const a = widgetPosition(left);
      const b = widgetPosition(right);
      return integerOr(a.y, 0) - integerOr(b.y, 0) || integerOr(a.x, 0) - integerOr(b.x, 0);
    });

    return {
      definition: {
        schema_version: "dashboard.v1",
        dashboard: {
          title: source.dashboard_title || source.title || "Dashboard",
          description: source.description || source.ai_summary || "",
          status: source.status || "",
        },
        layout: source.layout || { columns: 12, row_height: 80 },
        filters,
        widgets,
      },
      data,
      meta: {
        name: source.name,
        quality_score: source.quality_score,
        ai_spec: source.ai_spec,
        clean_dataset: source.clean_dataset,
      },
    };
  }

  function normalizeChartData(rawData) {
    const data = rawData || {};
    if (Array.isArray(data.series)) {
      const series = data.series.map((point) => ({
        name: String(point.name ?? point.label ?? ""),
        value: toNumber(point.value) ?? 0,
      }));
      return {
        labels: series.map((point) => point.name),
        values: series.map((point) => point.value),
        series,
      };
    }

    const labels = data.labels || data.bins || [];
    const values = data.values || [];
    const series = labels.map((label, index) => ({
      name: String(label ?? ""),
      value: toNumber(values[index]) ?? 0,
    }));
    return {
      labels: series.map((point) => point.name),
      values: series.map((point) => point.value),
      series,
    };
  }

  function buildChartOptions(widget, rawData) {
    const type = normalizeWidgetType(widget);
    const data = normalizeChartData(rawData);
    const display = getWidgetDisplay(widget, rawData);
    const showLegend = display.show_legend !== false && display.showLegend !== false;
    const title = widget.title || "Graphique";
    const colors = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#475569", "#be185d"];

    if (!CHART_TYPES.has(type)) {
      return { error: `Type de graphique non supporté: ${type}` };
    }

    if (type === "pie_chart" || type === "donut_chart") {
      return {
        color: colors,
        tooltip: {
          trigger: "item",
          formatter(params) {
            const value = formatValue(params.value, display);
            const percent = Number.isFinite(params.percent) ? `${params.percent.toFixed(1)}%` : "";
            return `${params.marker}${params.name}<br/><strong>${value}</strong>${percent ? ` (${percent})` : ""}`;
          },
        },
        legend: {
          show: showLegend,
          type: "scroll",
          bottom: 0,
          left: "center",
          itemWidth: 10,
          itemHeight: 10,
          textStyle: { color: "#475467", fontSize: 11 },
        },
        series: [
          {
            name: title,
            type: "pie",
            radius: type === "donut_chart" ? ["45%", "70%"] : ["0%", "70%"],
            center: ["50%", showLegend ? "43%" : "50%"],
            avoidLabelOverlap: true,
            label: {
              show: data.series.length <= 8,
              formatter: "{b}",
              color: "#344054",
              overflow: "truncate",
              width: 110,
            },
            labelLine: { show: data.series.length <= 8 },
            data: data.series,
          },
        ],
      };
    }

    const isLine = type === "line_chart";
    return {
      color: colors,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: isLine ? "line" : "shadow" },
        valueFormatter(value) {
          return formatValue(value, display);
        },
      },
      legend: {
        show: showLegend,
        top: 0,
        right: 0,
        textStyle: { color: "#475467", fontSize: 11 },
      },
      grid: {
        left: 12,
        right: 18,
        top: showLegend ? 36 : 18,
        bottom: 12,
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: data.labels,
        axisTick: { alignWithLabel: true },
        axisLabel: {
          color: "#667085",
          interval: 0,
          rotate: data.labels.length > 8 ? 25 : 0,
          overflow: "truncate",
          width: 92,
        },
        axisLine: { lineStyle: { color: "#d0d5dd" } },
      },
      yAxis: {
        type: "value",
        axisLabel: {
          color: "#667085",
          formatter(value) {
            return formatCompactValue(value, display);
          },
        },
        splitLine: { lineStyle: { color: "#eef2f7" } },
      },
      series: [
        {
          name: title,
          type: isLine ? "line" : "bar",
          data: data.values,
          smooth: isLine,
          symbolSize: isLine ? 7 : 0,
          barMaxWidth: 42,
          lineStyle: { width: 3 },
          areaStyle: isLine ? { opacity: 0.08 } : undefined,
        },
      ],
    };
  }

  function buildGridStyle(widget, layout) {
    const position = widgetPosition(widget);
    const columns = Math.max(1, integerOr(layout && layout.columns, 12));
    const rowHeight = Math.max(56, integerOr(layout && layout.row_height, 80));
    const x = Math.max(0, Math.min(columns - 1, integerOr(position.x, 0)));
    const w = Math.max(1, Math.min(columns - x, integerOr(position.w, columns)));
    const h = Math.max(2, integerOr(position.h, 4));
    return {
      gridColumn: `${x + 1} / span ${w}`,
      minHeight: `${h * rowHeight}px`,
    };
  }

  function isDevelopment() {
    if (typeof frappe !== "undefined" && frappe.boot) {
      return Boolean(frappe.boot.developer_mode);
    }
    return typeof process !== "undefined" && process.env && process.env.NODE_ENV !== "production";
  }

  return {
    formatValue,
    normalizeDashboardPayload,
    normalizeWidgetType,
    normalizeChartData,
    buildChartOptions,
    buildGridStyle,
    getWidgetDisplay,
    widgetId,
    isDevelopment,
  };
});
