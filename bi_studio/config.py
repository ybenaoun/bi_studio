MAX_IMPORT_ROWS = 1000

ALLOWED_IMPORT_EXTENSIONS = [".xlsx", ".xls"]

ALLOWED_DATASET_EXPORT_EXTENSIONS = [".xlsx"]

ALLOWED_DASHBOARD_EXPORT_EXTENSIONS = [".png"]

COHERE_API_KEY_ENV = "COHERE_API_KEY"

COHERE_SITE_CONFIG_KEY = "cohere_api_key"

DEFAULT_TOP_N = 10

SUPPORTED_CALCULATIONS = {
    "Total": "SUM",
    "Moyenne": "AVG",
    "Minimum": "MIN",
    "Maximum": "MAX",
    "Nombre": "COUNT",
    "Nombre unique": "COUNT DISTINCT",
}

SUPPORTED_CHART_TYPES = [
    "Bar",
    "Line",
    "Pie",
    "Donut",
    "Histogram",
    "Gauge",
    "Combined",
    "Table",
    "KPI Card",
]
