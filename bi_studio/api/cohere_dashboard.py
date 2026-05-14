"""Cohere adapter for dashboard spec generation.

Cohere receives enriched column_metadata + user_intent and must return a
complete dashboard.v1 JSON spec (schema_version, title, layout, widgets).
The backend then validates, removes redundancies, and calculates all values.
Cohere never computes sum/avg/count/etc. — it only designs the structure.

Fallback: if Cohere fails or returns an invalid spec, the backend builds
a deterministic dashboard from a small intent (legacy behaviour preserved).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import frappe
from frappe.utils import now_datetime

from bi_studio.utils.dashboard_intent import parse_dashboard_intent

FASTAPI_MESSAGE_LIMIT = 20_000
PROMPT_CHAR_LIMIT = 14_000
COHERE_MAX_TOKENS = 2_500

SYSTEM_MESSAGE = (
    "Tu es un expert BI qui génère des spécifications de dashboard au format dashboard.v1.\n"
    "Génère UNIQUEMENT le JSON brut valide, sans markdown, sans explication, sans commentaire.\n"
    "Ne calcule JAMAIS les valeurs finales (sum, avg, count, etc.) — le backend les calcule.\n"
    "N'utilise que les colonnes listées dans COLONNES DISPONIBLES.\n"
    "N'invente aucune colonne absente de cette liste.\n"
    "Génère des ids en snake_case (ex: total_salary, salary_by_department)."
)

_DASHBOARD_V1_FORMAT = """\
FORMAT OBLIGATOIRE dashboard.v1 :
{
  "schema_version": "dashboard.v1",
  "title": "Titre en français",
  "layout": {"columns": 12, "row_height": 80},
  "widgets": [
    {
      "id": "total_metric",
      "type": "kpi_card",
      "title": "Total Métrique",
      "position": {"x": 0, "y": 0, "w": 3, "h": 2},
      "data": {"source": "main", "metric": "nom_colonne", "aggregation": "sum"},
      "format": {"type": "number", "decimals": 2}
    },
    {
      "id": "metric_by_dim",
      "type": "bar_chart",
      "title": "Métrique par Dimension",
      "position": {"x": 0, "y": 2, "w": 6, "h": 4},
      "data": {"source": "main", "x": "dim_colonne", "y": "metric_colonne", "aggregation": "sum"},
      "options": {"orientation": "vertical", "show_legend": true, "stacked": false}
    },
    {
      "id": "metric_trend",
      "type": "line_chart",
      "title": "Évolution Métrique",
      "position": {"x": 6, "y": 2, "w": 6, "h": 4},
      "data": {"source": "main", "x": "date_colonne", "y": "metric_colonne", "aggregation": "sum"},
      "options": {"show_legend": true}
    },
    {
      "id": "metric_share",
      "type": "pie_chart",
      "title": "Répartition Métrique",
      "position": {"x": 0, "y": 6, "w": 4, "h": 4},
      "data": {"source": "main", "category": "dim_colonne", "value": "metric_colonne", "aggregation": "sum"},
      "options": {"show_legend": true}
    },
    {
      "id": "details_table",
      "type": "data_table",
      "title": "Détails",
      "position": {"x": 0, "y": 10, "w": 12, "h": 5},
      "data": {"source": "main", "columns": ["col1", "col2", "col3"], "limit": 100}
    },
    {
      "id": "dim_filter",
      "type": "filter",
      "title": "Filtre Dimension",
      "position": {"x": 0, "y": 15, "w": 3, "h": 1},
      "data": {"source": "main", "metric": "dim_colonne"}
    }
  ]
}"""

_DASHBOARD_RULES = """\
RÈGLES STRICTES :
1. Utilise UNIQUEMENT les colonnes listées dans COLONNES DISPONIBLES.
2. Ne génère JAMAIS de valeurs calculées — seulement la structure JSON.
3. Maximum 8 widgets analytiques (hors filtre et data_table).
4. bar_chart et pie_chart avec (même dimension + même mesure + même agrégation) sont REDONDANTS — garde bar_chart.
5. kpi_card global (sans dimension) N'est PAS redondant avec un bar_chart groupé sur la même mesure.
6. Pour comparaison catégorielle → bar_chart. Pour séries temporelles → line_chart.
7. pie_chart uniquement si cardinalité faible (≤ 6 valeurs distinctes) et représentation en part de total.
8. Tous les ids doivent être en snake_case et uniques.
9. Grille 12 colonnes : x + w ≤ 12.
10. Agrégations autorisées : sum, avg, count, min, max.
11. Types de widgets autorisés : kpi_card, bar_chart, line_chart, pie_chart, data_table, filter.
12. Ne génère pas schema_version, layout ou position différents du format imposé."""


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16] + "\n...[tronqué]"


def _columns_with_types(schema: dict[str, Any]) -> list[dict[str, Any]]:
    columns = schema.get("columns") if isinstance(schema, dict) else []
    out: list[dict[str, Any]] = []
    for col in columns or []:
        if not isinstance(col, dict):
            continue
        entry: dict[str, Any] = {
            "name": col.get("name"),
            "type": col.get("type") or col.get("detected_type"),
            "semantic_type": col.get("semantic_type"),
            "label": col.get("label"),
        }
        # Enrich with statistics when available
        for stat in ("min", "max", "avg", "unique_count", "date_min", "date_max"):
            if col.get(stat) is not None:
                entry[stat] = col[stat]
        if col.get("sample_values"):
            entry["sample_values"] = col["sample_values"][:5]
        out.append(entry)
    return out


def _column_lines(columns: list[dict[str, Any]]) -> list[str]:
    lines = []
    for col in columns:
        parts = [
            f"- {_truncate_text(col.get('name'), 60)}",
            f"type={_truncate_text(col.get('type'), 20)}",
            f"semantic={_truncate_text(col.get('semantic_type'), 20)}",
        ]
        if col.get("label"):
            parts.append(f"label={_truncate_text(col['label'], 50)}")
        stats = []
        for stat in ("min", "max", "avg"):
            if col.get(stat) is not None:
                stats.append(f"{stat}={col[stat]}")
        if col.get("unique_count") is not None:
            stats.append(f"unique={col['unique_count']}")
        if col.get("sample_values"):
            vals = ", ".join(str(v) for v in col["sample_values"][:3])
            stats.append(f"samples=[{vals}]")
        if stats:
            parts.append(f"[{', '.join(stats)}]")
        lines.append(" | ".join(parts))
    return lines


def _bounded_lines(lines: list[str], max_chars: int) -> str:
    kept: list[str] = []
    used = 0
    for line in lines:
        line_len = len(line) + 1
        if kept and used + line_len > max_chars:
            break
        kept.append(line if line_len <= max_chars else _truncate_text(line, max_chars))
        used += min(line_len, max_chars)
    omitted = max(len(lines) - len(kept), 0)
    if omitted:
        kept.append(f"... {omitted} colonnes omises (limite de taille atteinte).")
    return "\n".join(kept)


def _user_intent_section(user_intent: dict[str, Any] | None) -> str:
    if not user_intent:
        return ""
    parts: list[str] = ["OBJECTIFS UTILISATEUR :"]
    goals = user_intent.get("analysis_goals")
    if goals:
        parts.append(f"  Axes d'analyse : {', '.join(str(g) for g in goals)}")
    kpis = user_intent.get("preferred_kpis")
    if kpis:
        parts.append(f"  KPIs souhaités : {', '.join(str(k) for k in kpis)}")
    dims = user_intent.get("preferred_dimensions")
    if dims:
        parts.append(f"  Dimensions préférées : {', '.join(str(d) for d in dims)}")
    vizs = user_intent.get("preferred_visualizations")
    if vizs:
        parts.append(f"  Visualisations préférées : {', '.join(str(v) for v in vizs)}")
    return "\n".join(parts)


def _build_dashboard_prompt(
    metadata: dict[str, Any],
    columns_with_types: list[dict[str, Any]],
    user_intent: dict[str, Any] | None,
) -> str:
    intent_section = _user_intent_section(user_intent)
    prefix = (
        f"Génère une spécification de dashboard dashboard.v1.\n\n"
        f"SOURCE : {metadata['data_source_name']}\n"
        f"TITRE SUGGÉRÉ : {metadata['dashboard_objective']}\n"
        f"LIGNES : {metadata['row_count']}  |  QUALITÉ : {metadata['quality_score']}/100\n"
    )
    if intent_section:
        prefix += f"\n{intent_section}\n"

    suffix = f"\nCOLONNES DISPONIBLES :\n{{columns}}\n\n{_DASHBOARD_RULES}\n\n{_DASHBOARD_V1_FORMAT}"

    # Budget: total limit minus fixed parts
    col_budget = max(
        PROMPT_CHAR_LIMIT - len(prefix) - len(suffix.replace("{columns}", "")) - 200,
        800,
    )
    col_text = _bounded_lines(_column_lines(columns_with_types), col_budget)
    full = prefix + suffix.replace("{columns}", col_text)
    return _truncate_text(full, PROMPT_CHAR_LIMIT)


def build_cohere_dashboard_prompt(
    clean_dataset_doc: Any,
    user_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the prompt sent to Cohere asking for a full dashboard.v1 spec."""
    schema = json.loads(clean_dataset_doc.schema_json or "{}")
    metadata = {
        "dataset_title": clean_dataset_doc.dataset_title,
        "data_source_name": "main",
        "dashboard_objective": clean_dataset_doc.dataset_title or "Créer un dashboard utile pour ce jeu de données.",
        "row_count": int(clean_dataset_doc.row_count or 0),
        "column_count": int(clean_dataset_doc.column_count or 0),
        "quality_score": float(clean_dataset_doc.quality_score or 0),
    }
    columns_with_types = _columns_with_types(schema)
    return {
        "system": SYSTEM_MESSAGE,
        "user": _build_dashboard_prompt(metadata, columns_with_types, user_intent),
        "metadata": metadata,
        "schema": schema,
        "columns_with_types": columns_with_types,
        "user_intent": user_intent or {},
    }


def _import_gateway():
    try:
        from custom_dashboard.services import ai_gateway  # type: ignore
        return ai_gateway
    except ImportError as exc:
        raise RuntimeError(
            "Le service IA Cohere requiert l'app 'custom_dashboard'. "
            "Installe-la dans le bench puis relance."
        ) from exc


def _safe_now_datetime():
    try:
        return now_datetime()
    except Exception:
        return datetime.now()


def _persist_spec(
    prompt_payload: dict[str, Any],
    clean_dataset_doc: Any,
    import_doc: Any | None,
    *,
    raw_text: str,
    response_json: dict[str, Any],
    model: str,
) -> Any:
    spec_doc = frappe.new_doc("BI AI Dashboard Spec")
    spec_doc.source_import = import_doc.name if import_doc else None
    spec_doc.clean_dataset = clean_dataset_doc.name
    spec_doc.provider = "Cohere"
    spec_doc.model = model
    spec_doc.created_on = _safe_now_datetime()
    spec_doc.prompt_json = json.dumps(prompt_payload, ensure_ascii=False, default=str)
    spec_doc.response_json = json.dumps(response_json, ensure_ascii=False, default=str)
    spec_doc.raw_response_text = raw_text[:50_000]
    spec_doc.validation_status = "Pending"
    spec_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return spec_doc


def call_cohere_dashboard_generation(
    prompt_payload: dict[str, Any],
    clean_dataset_doc: Any,
    import_doc: Any | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Ask Cohere for a full dashboard.v1 spec and persist the raw response.

    Returns (spec_doc, parsed_response).
    parsed_response may be:
      - a full dashboard.v1 dict (schema_version present)
      - a small intent dict (legacy fallback parsing)
      - an empty dict on complete parse failure
    """
    gateway = _import_gateway()
    response = gateway.generate_with_cohere(
        system_message=prompt_payload["system"],
        user_message=prompt_payload["user"],
        language="fr",
        temperature=0,
        max_tokens=COHERE_MAX_TOKENS,
        timeout=300,
    )
    raw_text = str(response.get("response") or "")

    # Try to parse as full dashboard.v1 first, then as small intent (backward compat)
    try:
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception:
        # Gateway may wrap a failed parse as {"error": "...", "raw": "..."}
        parsed = parse_dashboard_intent(raw_text) or {}

    spec_doc = _persist_spec(
        prompt_payload,
        clean_dataset_doc,
        import_doc,
        raw_text=raw_text,
        response_json=parsed,
        model=response.get("model") or "command-a",
    )
    return spec_doc, parsed


def create_fallback_dashboard_spec(
    prompt_payload: dict[str, Any],
    clean_dataset_doc: Any,
    import_doc: Any | None = None,
    *,
    reason: str = "",
) -> tuple[Any, dict[str, Any]]:
    """Persist an empty response so the backend builds a deterministic dashboard."""
    raw_text = json.dumps(
        {"fallback": "deterministic_backend_spec", "reason": str(reason)[:500]},
        ensure_ascii=False,
    )
    spec_doc = _persist_spec(
        prompt_payload,
        clean_dataset_doc,
        import_doc,
        raw_text=raw_text,
        response_json={},
        model="deterministic-backend",
    )
    return spec_doc, {}
