# Pipeline ETL de `bi_studio/api`

Ce document décrit les pipelines ETL présents dans le dossier `bi_studio/api`.

Il y a deux chemins principaux :

1. **Pipeline automatique Excel -> dataset nettoyé -> dashboard IA** : `excel_pipeline.py`
2. **Pipeline manuel / historique import -> profiling -> mapping -> transformation** : `importer.py`, `profiling.py`, `etl.py`

Le pipeline automatique est le point d'entrée moderne recommandé. Le pipeline manuel reste utile pour comprendre les étapes séparées et les anciens endpoints.

---

## Vue d'ensemble

```text
Utilisateur / Frontend
        |
        v
run_excel_to_dashboard_pipeline(file_url, dataset_title, sheet_name, header_row)
        |
        v
BI Dataset Import status = Uploaded
        |
        v
frappe.enqueue(excel_to_dashboard_job)
        |
        v
+---------+      +-------+      +----------------+      +---------+      +------+
| Extract | ---> | Clean | ---> | Rename/Profile | ---> |  Load   | ---> |  AI  |
+---------+      +-------+      +----------------+      +---------+      +------+
    |              |                 |                     |              |
    v              v                 v                     v              v
Excel brut     DataFrame        schema/profile       BI Clean       Dashboard
lu avec        nettoyé          + quality score      Dataset        BI généré
pandas
```

Les logs de progression sont persistés dans le doctype **BI ETL Job Log** via `log_step`.

---

## Pipeline automatique : `excel_pipeline.py`

### Point d'entrée

Le frontend appelle `run_excel_to_dashboard_pipeline`. Cette fonction crée un document **BI Dataset Import**, puis lance un job de fond.

```python
@frappe.whitelist()
def run_excel_to_dashboard_pipeline(
    file_url: str,
    dataset_title: str | None = None,
    sheet_name: str | None = None,
    header_row: int | None = None,
) -> dict[str, str]:
    """Create a BI Dataset Import doc and enqueue the full pipeline."""
    if not file_url:
        frappe.throw("Le fichier Excel est obligatoire.")

    doc = frappe.new_doc("BI Dataset Import")
    doc.import_file = file_url
    doc.dataset_title = dataset_title or "Jeu de données"
    doc.dataset_name = dataset_title or f"Import {now_datetime().strftime('%Y%m%d%H%M%S')}"
    doc.original_filename = file_url.rsplit("/", 1)[-1]
    doc.owner_user = frappe.session.user
    doc.selected_sheet = sheet_name or ""
    doc.header_row = int(header_row) if header_row else 0
    doc.status = "Uploaded"
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "bi_studio.api.excel_pipeline.excel_to_dashboard_job",
        queue=PIPELINE_QUEUE,
        timeout=600,
        import_name=doc.name,
    )

    return {"import_name": doc.name, "status": "Uploaded"}
```

### Orchestration du job

`excel_to_dashboard_job` orchestre les étapes :

1. Extract
2. Clean
3. Rename Columns
4. Profile
5. Save clean dataset
6. Cohere Prompt
7. Validate AI JSON
8. Build Dashboard

Extrait de l'orchestration :

```python
def excel_to_dashboard_job(import_name: str) -> None:
    """Background job: run the full pipeline end-to-end."""
    from bi_studio.api.cohere_dashboard import (
        build_cohere_dashboard_prompt,
        call_cohere_dashboard_generation,
        create_fallback_dashboard_spec,
    )
    from bi_studio.api.dashboard_builder import build_dashboard_from_spec

    import_doc = frappe.get_doc("BI Dataset Import", import_name)

    def _fail(step: str, exc: Exception) -> None:
        log_step(import_name, step, "Failed", message=str(exc), traceback_text=traceback.format_exc())
        import_doc.reload()
        import_doc.status = "Failed"
        import_doc.error_message = f"[{step}] {exc}"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()
```

Chaque étape met à jour le statut de l'import, écrit un log, puis commit les changements.

---

## E - Extract

### Objectif

L'étape **Extract** lit le fichier Excel source, détecte la meilleure feuille, détecte la ligne d'en-tête, supprime les lignes/colonnes entièrement vides, puis stocke les métadonnées brutes dans **BI Dataset Import**.

### Code : détection de la ligne d'en-tête

Fichier : `api/excel_pipeline.py`

```python
def _detect_header_row(df_no_header: pd.DataFrame, max_scan: int = 25) -> int:
    """Pick the first row that has multiple non-empty text cells AND is followed
    by data. Returns 0-indexed header row.
    """
    best_row = 0
    best_score = -1
    rows_to_scan = min(max_scan, len(df_no_header.index))
    for i in range(rows_to_scan):
        row = df_no_header.iloc[i]
        non_empty = row.dropna()
        if non_empty.empty:
            continue
        text_cells = sum(1 for v in non_empty if isinstance(v, str) and v.strip())
        non_empty_count = len(non_empty)
        score = text_cells * 2 + non_empty_count
        if i + 1 < len(df_no_header.index):
            next_non_empty = df_no_header.iloc[i + 1].dropna().size
            score += min(next_non_empty, 10)
        if score > best_score:
            best_score = score
            best_row = i
    return best_row
```

### Code : choix de la feuille Excel

Fichier : `api/excel_pipeline.py`

```python
def _pick_best_sheet(excel: pd.ExcelFile) -> str:
    """Pick the sheet with the most non-empty cells."""
    best_sheet = excel.sheet_names[0]
    best_score = -1
    for sheet in excel.sheet_names:
        try:
            sample = pd.read_excel(excel, sheet_name=sheet, header=None, nrows=50)
            score = int(sample.notna().sum().sum())
            if score > best_score:
                best_score = score
                best_sheet = sheet
        except Exception:
            continue
    return best_sheet
```

### Code : extraction Excel complète

Fichier : `api/excel_pipeline.py`

```python
def extract_excel(import_doc: Any) -> pd.DataFrame:
    """Read the Excel file, detect sheet + header row, return cleaned-of-empty df.

    Updates the import_doc with:
        selected_sheet, header_row, row_count_raw, column_count_raw,
        raw_preview_json, detected_sheets_json
    """
    _, file_path = resolve_file(file_url=import_doc.import_file)

    excel = pd.ExcelFile(file_path)
    detected_sheets = excel.sheet_names

    sheet_name = import_doc.selected_sheet or _pick_best_sheet(excel)
    if sheet_name not in detected_sheets:
        sheet_name = detected_sheets[0]

    raw = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
    if import_doc.header_row and int(import_doc.header_row) > 0:
        header_row = int(import_doc.header_row) - 1
    else:
        header_row = _detect_header_row(raw)

    df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.dropna(axis=1, how="all")

    import_doc.selected_sheet = sheet_name
    import_doc.header_row = header_row + 1
    import_doc.row_count_raw = int(len(df.index))
    import_doc.column_count_raw = int(len(df.columns))
    import_doc.raw_preview_json = json.dumps(
        dataframe_preview(df, limit=50), default=str, ensure_ascii=False
    )
    import_doc.detected_sheets_json = json.dumps(detected_sheets, ensure_ascii=False)
    return df
```

### Code : appel dans le job

```python
log_step(import_name, "Extract", "Started")
import_doc.status = "Extracting"
import_doc.save(ignore_permissions=True)
frappe.db.commit()
df_raw = extract_excel(import_doc)
import_doc.save(ignore_permissions=True)
frappe.db.commit()
log_step(
    import_name,
    "Extract",
    "Success",
    message=f"{import_doc.row_count_raw} lignes, {import_doc.column_count_raw} colonnes",
)
```

---

## T - Transform

La transformation est découpée en trois sous-étapes :

1. **Clean** : nettoyage des valeurs et suppression des doublons exacts.
2. **Rename Columns** : renommage des colonnes en `snake_case` lisible.
3. **Profile** : profilage des colonnes, schéma et score qualité.

### Transform 1 : Clean

Fichier : `api/etl_cleaning.py`

Cette étape :

- supprime les lignes et colonnes totalement vides ;
- trim les textes ;
- remplace les marqueurs de valeurs nulles ;
- supprime les doublons exacts ;
- détecte les colonnes constantes ;
- tente de parser les nombres en formats US et français ;
- tente de parser les dates sur les colonnes dont le nom suggère une date ;
- retourne un log et les erreurs de conversion.

```python
def clean_dataset(df: pd.DataFrame) -> dict[str, Any]:

    log: list[str] = []
    if df is None or df.empty:
        return {
            "dataframe": df if df is not None else pd.DataFrame(),
            "conversion_errors": {},
            "removed_empty_columns": [],
            "removed_constant_columns": [],
            "duplicate_rows_removed": 0,
            "log": ["Aucune donnée fournie."],
        }

    df = df.copy()

    df, removed_empty = _drop_empty_axes(df)
    if removed_empty:
        log.append(f"Colonnes vides supprimées: {', '.join(removed_empty)}")

    df = _strip_text_columns(df)
    df = _normalize_nulls(df)

    before_rows = len(df.index)
    df = df.drop_duplicates().reset_index(drop=True)
    duplicates_removed = before_rows - len(df.index)
    if duplicates_removed:
        log.append(f"Doublons exacts supprimés: {duplicates_removed}")

    constants: list[str] = []
    for col in df.columns:
        non_null = df[col].dropna()
        if not non_null.empty and non_null.nunique() <= 1:
            constants.append(str(col))
    if constants:
        log.append(f"Colonnes constantes détectées: {', '.join(constants)}")

    conversion_errors: dict[str, int] = {}
    for col in list(df.columns):
        if not _is_text_dtype(df[col]):
            continue
        series = df[col]
        non_null = series.dropna()
        if non_null.empty:
            continue
        looks_numeric_ratio = non_null.astype(str).str.match(
            r"^\s*-?\(?\s*[$€£¥₦]?\s*[\d\s.,\xa0]+%?\)?\s*$"
        ).mean()
        if looks_numeric_ratio >= 0.6:
            parsed = parse_number_series_locale(series)
            failures = int(((parsed.isna()) & (series.notna())).sum())
            success_ratio = float(parsed.notna().sum() / max(non_null.size, 1))
            if success_ratio >= 0.6:
                df[col] = pd.to_numeric(parsed, errors="coerce")
                if failures:
                    conversion_errors[str(col)] = failures
                    log.append(f"{failures} valeurs non numériques dans '{col}'")

    date_keywords = ("date", "start", "end", "joining", "joined", "created", "updated")
    for col in list(df.columns):
        col_lower = str(col).lower()
        if not any(k in col_lower for k in date_keywords):
            continue
        if df[col].dtype != object:
            continue
        parsed = parse_date_series(df[col])
        success_ratio = float(parsed.notna().sum() / max(df[col].notna().sum(), 1))
        if success_ratio >= 0.5:
            failures = int(((parsed.isna()) & (df[col].notna())).sum())
            df[col] = parsed
            if failures:
                conversion_errors[str(col)] = conversion_errors.get(str(col), 0) + failures
                log.append(f"{failures} dates invalides dans '{col}'")

    return {
        "dataframe": df,
        "conversion_errors": conversion_errors,
        "removed_empty_columns": removed_empty,
        "removed_constant_columns": constants,
        "duplicate_rows_removed": duplicates_removed,
        "log": log,
    }
```

Parsing numérique localisé utilisé par le nettoyage :

```python
def parse_number_series_locale(series: pd.Series) -> pd.Series:
    """Locale-aware number parser used by the intelligent ETL pipeline."""
    return series.map(_parse_locale_number)
```

### Transform 2 : Rename Columns

Fichier : `api/column_renamer.py`

Cette étape convertit les colonnes Excel brutes en noms techniques propres et génère des labels français pour l'UI.

```python
def rename_columns_to_readable_names(df: pd.DataFrame) -> dict[str, Any]:

    if df is None or df.empty:
        return {"dataframe": df, "column_mapping": {}, "column_labels": {}}

    new_cols: list[str] = []
    mapping: dict[str, str] = {}
    labels: dict[str, str] = {}
    seen: dict[str, int] = {}

    for original in df.columns:
        original_str = str(original)
        candidate = _resolve_hint(original_str) or _to_snake_case(original_str)

        base = candidate
        if base in seen:
            seen[base] += 1
            candidate = f"{base}_{seen[base]}"
        else:
            seen[base] = 1

        new_cols.append(candidate)
        mapping[original_str] = candidate
        labels[candidate] = _french_label_from(candidate)

    df_renamed = df.copy()
    df_renamed.columns = new_cols
    return {
        "dataframe": df_renamed,
        "column_mapping": mapping,
        "column_labels": labels,
    }
```

### Transform 3 : Profile et schéma

Fichier : `api/dataset_profiler.py`

Le profilage détecte le type technique, le rôle sémantique, les valeurs manquantes, les exemples, les bornes, la moyenne et les avertissements.

```python
def profile_dataset(
    df: pd.DataFrame,
    column_mapping: dict[str, str] | None = None,
    column_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    
    column_mapping = column_mapping or {}
    column_labels = column_labels or {}
    inverse_mapping = {v: k for k, v in column_mapping.items()}

    row_count = int(len(df.index))
    column_profiles: list[dict[str, Any]] = []
    for col in df.columns:
        name = str(col)
        original = inverse_mapping.get(name, name)
        label = column_labels.get(name, name.replace("_", " ").capitalize())
        column_profiles.append(
            _column_profile(df[col], name, original, label, row_count)
        )

    return {
        "row_count": row_count,
        "column_count": int(len(df.columns)),
        "columns": column_profiles,
    }
```

Le schéma compact alimente ensuite le prompt Cohere et le validateur JSON :

```python
def schema_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    
    columns = profile.get("columns") or []
    return {
        "columns": [
            {
                "name": col["name"],
                "original_name": col.get("original_name"),
                "label": col.get("label"),
                "type": col.get("type"),
                "semantic_type": col.get("semantic_type"),
                "missing_rate": col.get("missing_rate"),
                "unique_count": col.get("unique_count"),
                "sample_values": col.get("sample_values"),
            }
            for col in columns
            if col.get("include", True)
        ]
    }
```

### Code : appels Transform dans le job

Fichier : `api/excel_pipeline.py`

```python
log_step(import_name, "Clean", "Started")
import_doc.status = "Cleaning"
import_doc.save(ignore_permissions=True)
frappe.db.commit()
cleaning = clean_dataset(df_raw)
df_clean = cleaning["dataframe"]
log_step(
    import_name, "Clean", "Success",
    message="\n".join(cleaning.get("log") or []) or "Nettoyage terminé.",
)
```

```python
log_step(import_name, "Rename Columns", "Started")
renamed = rename_columns_to_readable_names(df_clean)
df_clean = renamed["dataframe"]
mapping = renamed["column_mapping"]
labels = renamed["column_labels"]
log_step(
    import_name, "Rename Columns", "Success",
    message=f"{len(mapping)} colonnes renommées",
)
```

```python
log_step(import_name, "Profile", "Started")
import_doc.status = "Profiling"
import_doc.save(ignore_permissions=True)
frappe.db.commit()
profile = profile_dataset(df_clean, mapping, labels)
schema = schema_from_profile(profile)
quality = compute_dataset_quality(
    df_clean,
    profile_columns=profile.get("columns"),
    conversion_errors=cleaning.get("conversion_errors"),
)
log_step(
    import_name, "Profile", "Success",
    message=f"Score qualité: {quality.get('score')}",
)
```

---

## L - Load

### Objectif

L'étape **Load** persiste le dataset nettoyé et ses métadonnées dans le doctype **BI Clean Dataset**.

Le pipeline stocke :

- le titre du dataset ;
- l'import source ;
- le score qualité ;
- le nombre de lignes et colonnes ;
- le schéma JSON ;
- le mapping original -> normalisé ;
- les labels ;
- le profil complet ;
- un aperçu ;
- les données nettoyées en JSON.

### Code : sauvegarde du dataset nettoyé

Fichier : `api/excel_pipeline.py`

```python
def save_clean_dataset(
    import_doc: Any,
    df: pd.DataFrame,
    schema: dict[str, Any],
    profile: dict[str, Any],
    mapping: dict[str, str],
    labels: dict[str, str],
    quality: dict[str, Any],
) -> Any:
    
    doc = frappe.new_doc("BI Clean Dataset")
    doc.dataset_title = import_doc.dataset_title or import_doc.dataset_name or "Jeu de données"
    doc.source_import = import_doc.name
    doc.quality_score = float(quality.get("score", 0))
    doc.row_count = int(len(df.index))
    doc.column_count = int(len(df.columns))
    doc.created_on = now_datetime()
    doc.schema_json = json.dumps(schema, ensure_ascii=False, default=str)
    doc.column_mapping_json = json.dumps(mapping, ensure_ascii=False)
    doc.column_labels_json = json.dumps(labels, ensure_ascii=False)
    doc.profile_json = json.dumps(profile, ensure_ascii=False, default=str)
    doc.preview_json = json.dumps(
        dataframe_preview(df, limit=50), ensure_ascii=False, default=str
    )
    doc.clean_data_json = json.dumps(
        df.where(pd.notnull(df), None).to_dict(orient="records"),
        ensure_ascii=False,
        default=str,
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc
```

### Code : appel Load dans le job

Fichier : `api/excel_pipeline.py`

```python
clean_dataset_doc = save_clean_dataset(
    import_doc, df_clean, schema, profile, mapping, labels, quality,
)
import_doc.reload()
import_doc.clean_dataset = clean_dataset_doc.name
import_doc.row_count_cleaned = int(len(df_clean.index))
import_doc.column_count_cleaned = int(len(df_clean.columns))
import_doc.cleaned_preview_json = json.dumps(
    dataframe_preview(df_clean, limit=50),
    ensure_ascii=False, default=str,
)
import_doc.save(ignore_permissions=True)
frappe.db.commit()
```

### Code : rechargement du dataset nettoyé

Fichier : `api/excel_pipeline.py`

```python
def load_clean_dataframe(clean_dataset_doc: Any) -> pd.DataFrame:
    """Rehydrate a pandas DataFrame from a BI Clean Dataset doc."""
    raw = clean_dataset_doc.clean_data_json or "[]"
    records = json.loads(raw)
    return pd.DataFrame(records)
```

---

## Après ETL : génération du dashboard

Ce qui suit n'est pas strictement ETL, mais fait partie du pipeline automatique complet.

### Prompt IA et fallback déterministe

Fichier : `api/excel_pipeline.py`

```python
log_step(import_name, "Cohere Prompt", "Started")
import_doc.status = "Waiting AI"
import_doc.save(ignore_permissions=True)
frappe.db.commit()

prompt_payload = build_cohere_dashboard_prompt(clean_dataset_doc)
try:
    spec_doc, spec_json = call_cohere_dashboard_generation(
        prompt_payload, clean_dataset_doc=clean_dataset_doc, import_doc=import_doc,
    )
    message = "Intention Cohere reçue."
except Exception as cohere_exc:
    frappe.log_error(
        title="BI Pipeline: fallback intention déterministe",
        message=traceback.format_exc(),
    )
    spec_doc, spec_json = create_fallback_dashboard_spec(
        prompt_payload,
        clean_dataset_doc=clean_dataset_doc,
        import_doc=import_doc,
        reason=str(cohere_exc),
    )
    message = f"IA indisponible ou invalide, intention déterministe utilisée: {cohere_exc}"
log_step(import_name, "Cohere Prompt", "Success", message=message[:5000])
```

### Validation JSON

```python
log_step(import_name, "Validate AI JSON", "Started")
spec_json = _build_validated_dashboard_from_intent(prompt_payload, spec_doc, schema)
spec_doc.validation_status = "Valid"
spec_doc.validated_json = json.dumps(spec_json, ensure_ascii=False, default=str)
spec_doc.save(ignore_permissions=True)
frappe.db.commit()
log_step(import_name, "Validate AI JSON", "Success", message="JSON validé.")
```

### Construction du dashboard

```python
log_step(import_name, "Build Dashboard", "Started")
dashboard_doc = build_dashboard_from_spec(clean_dataset_doc, spec_doc)
import_doc.reload()
import_doc.created_dashboard = dashboard_doc.name
import_doc.ai_dashboard_spec = spec_doc.name
import_doc.status = "Dashboard Ready"
import_doc.save(ignore_permissions=True)
frappe.db.commit()
log_step(import_name, "Build Dashboard", "Success", message=dashboard_doc.name)
```

---

## Pipeline manuel / historique

Le dossier contient aussi un flux plus découpé :

```text
upload_and_preview_excel
        |
        v
start_profiling -> profile_dataset_job
        |
        v
save_semantic_mapping
        |
        v
start_transform -> transform_dataset_job
        |
        v
BI Dataset + tables analytiques
```

Ce flux persiste dans **BI Dataset** et appelle `create_analytical_tables`, alors que le pipeline automatique moderne persiste d'abord dans **BI Clean Dataset**.

### Extract manuel : preview et import

Fichier : `api/importer.py`

```python
def read_import_dataframe(dataset_import):
    file_doc, path = get_excel_file(file_url=dataset_import.import_file)
    sheet_name = dataset_import.selected_sheet
    header_row = normalize_header_row(dataset_import.header_row) or detect_header_row(path, sheet_name)
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, header=header_row - 1, dtype=object)
    except Exception as exc:
        frappe.throw(_("Lecture Excel impossible: {0}").format(str(exc)))
    if len(df.index) > MAX_IMPORT_ROWS:
        frappe.throw(_(f"Le fichier dépasse la limite autorisée de {MAX_IMPORT_ROWS} lignes."))
    if len(df.columns) == 0:
        frappe.throw(_("Le fichier ne contient aucune colonne exploitable."))
    return file_doc, df
```

```python
@frappe.whitelist()
def upload_and_preview_excel(file_url, sheet_name=None, header_row=None):
    ensure_authenticated()
    file_doc, path = get_excel_file(file_url=file_url)
    workbook = get_workbook(path)
    sheets = workbook.sheet_names
    if not sheets:
        frappe.throw(_("Le fichier Excel ne contient aucune feuille."))
    selected_sheet = sheet_name if sheet_name in sheets else sheets[0]
    selected_header_row = normalize_header_row(header_row) or detect_header_row(path, selected_sheet)

    df = pd.read_excel(path, sheet_name=selected_sheet, header=selected_header_row - 1, dtype=object)

    normalized_df, original_labels = normalize_dataframe_columns(df)
    preview = dataframe_preview(normalized_df, PREVIEW_LIMIT)
    dataset_name = (file_doc.file_name or "Dataset").rsplit(".", 1)[0]

    dataset_import = frappe.get_doc(
        {
            "doctype": "BI Dataset Import",
            "import_file": file_doc.file_url,
            "dataset_name": dataset_name,
            "selected_sheet": selected_sheet,
            "header_row": selected_header_row,
            "status": "Uploaded",
            "row_count": len(normalized_df.index),
            "column_count": len(normalized_df.columns),
            "detected_sheets": to_json(sheets),
            "preview_data": to_json(
                {
                    "columns": [
                        {"label": original_labels[index] if index < len(original_labels) else column, "name": column}
                        for index, column in enumerate(normalized_df.columns)
                    ],
                    "rows": preview,
                }
            ),
            "import_log": _("Fichier téléversé et prévisualisé le {0}.").format(now_datetime()),
            "owner_user": frappe.session.user,
        }
    )
    dataset_import.insert(ignore_permissions=True)

    return import_payload(dataset_import)
```

### Profiling et mapping

Fichier : `api/profiling.py`

```python
@frappe.whitelist()
def start_profiling(dataset_import_name):
    dataset_import = ensure_dataset_import_access(dataset_import_name, write=True)
    dataset_import.status = "Profiling"
    dataset_import.error_message = None
    dataset_import.save(ignore_permissions=True)
    create_job_log(dataset_import.name, "Profiling", "Started", _("Profiling mis en file d'attente."))
    frappe.enqueue(
        "bi_studio.api.profiling.profile_dataset_job",
        queue="long",
        timeout=900,
        enqueue_after_commit=True,
        dataset_import_name=dataset_import.name,
    )
    return {"queued": True, "dataset_import": dataset_import.name}
```

```python
@frappe.whitelist()
def profile_dataset_job(dataset_import_name):
    dataset_import = frappe.get_doc("BI Dataset Import", dataset_import_name)
    log = create_job_log(dataset_import.name, "Profiling", "Started", _("Profiling démarré."))
    try:
        file_doc, raw_df = read_import_dataframe(dataset_import)
        normalized_df, profiles = profile_dataframe(raw_df)

        for name in frappe.get_all("BI Column Profile", filters={"dataset_import": dataset_import.name}, pluck="name"):
            frappe.delete_doc("BI Column Profile", name, ignore_permissions=True, force=True)

        for profile in profiles:
            frappe.get_doc(
                {
                    "doctype": "BI Column Profile",
                    "dataset_import": dataset_import.name,
                    "original_column_name": profile.original_column_name,
                    "normalized_column_name": profile.normalized_column_name,
                    "detected_type": profile.detected_type,
                    "semantic_role": profile.semantic_role,
                    "null_count": profile.null_count,
                    "null_rate": profile.null_rate,
                    "unique_count": profile.unique_count,
                    "sample_values": to_json(profile.sample_values),
                    "min_value": profile.min_value,
                    "max_value": profile.max_value,
                    "mean_value": profile.mean_value,
                    "confidence": profile.confidence,
                    "warnings": to_json(profile.warnings or []),
                }
            ).insert(ignore_permissions=True)

        dataset_import.status = "Mapping Required"
        dataset_import.row_count = len(normalized_df.index)
        dataset_import.column_count = len(normalized_df.columns)
        dataset_import.import_log = append_log(dataset_import.import_log, _("Profiling terminé: {0} colonnes.").format(len(profiles)))
        dataset_import.save(ignore_permissions=True)

        update_job_log(log, "Success", _("Profiling terminé."))
        ensure_initial_mapping(dataset_import, profiles)
        return import_payload(dataset_import, include_profiles=True)
    except Exception as exc:
        dataset_import.status = "Failed"
        dataset_import.error_message = str(exc)[:1000]
        dataset_import.import_log = append_log(dataset_import.import_log, _("Erreur profiling: {0}").format(str(exc)))
        dataset_import.save(ignore_permissions=True)
        update_job_log(log, "Failed", str(exc), traceback.format_exc())
        frappe.log_error(traceback.format_exc(), "BI Studio profiling failed")
        raise
```

Fichier : `api/etl.py`

```python
@frappe.whitelist()
def save_semantic_mapping(dataset_import_name, mapping_json):
    dataset_import = ensure_dataset_import_access(dataset_import_name, write=True)
    mapping = validate_mapping(dataset_import.name, from_json(mapping_json, mapping_json))
    existing = frappe.db.get_value("BI Semantic Mapping", {"dataset_import": dataset_import.name}, "name")
    payload = {
        "mapping_name": _("Mapping validé - {0}").format(dataset_import.dataset_name or dataset_import.name),
        "mapping_json": to_json(mapping),
        "validated_by": frappe.session.user,
        "validated_on": now_datetime(),
    }
    if existing:
        doc = frappe.get_doc("BI Semantic Mapping", existing)
        doc.update(payload)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "BI Semantic Mapping", "dataset_import": dataset_import.name, **payload})
        doc.insert(ignore_permissions=True)

    dataset_import.import_log = append_log(dataset_import.import_log, _("Mapping sémantique validé."))
    dataset_import.save(ignore_permissions=True)
    return {"mapping": doc.name, "dataset_import": dataset_import.name}
```

### Transform manuel : règles, conversion et DataFrame propre

Fichier : `api/etl.py`

```python
def apply_transform_rules(dataset_import_name, df):
    warnings = []
    for rule in frappe.get_all(
        "BI Transform Rule",
        filters={"dataset_import": dataset_import_name, "enabled": 1},
        fields=["column_name", "rule_type", "rule_config_json"],
        order_by="creation asc",
    ):
        column = rule.column_name
        config = from_json(rule.rule_config_json, {})
        if rule.rule_type != "Remove Duplicates" and column not in df.columns:
            warnings.append(f"Rule skipped, missing column: {column}")
            continue
        if rule.rule_type == "Trim Text":
            df[column] = clean_series(df[column]).map(lambda value: value.strip() if isinstance(value, str) else value)
        elif rule.rule_type == "Normalize Category":
            df[column] = df[column].map(normalize_category_value)
        elif rule.rule_type == "Convert Date":
            df[column] = parse_date_series(df[column]).dt.date
        elif rule.rule_type == "Convert Number":
            df[column] = parse_number_series(df[column])
        elif rule.rule_type == "Fill Missing":
            df[column] = df[column].where(pd.notnull(df[column]), config.get("value"))
        elif rule.rule_type == "Drop Column":
            df = df.drop(columns=[column])
        elif rule.rule_type == "Rename Column":
            new_name = config.get("new_name")
            if new_name:
                df = df.rename(columns={column: new_name})
        elif rule.rule_type == "Remove Duplicates":
            df = df.drop_duplicates().reset_index(drop=True)
        elif rule.rule_type == "Custom Formula":
            warnings.append("Custom Formula ignored: arbitrary formulas are disabled for security.")
    return df, warnings
```

```python
def build_clean_dataframe(df, mapped_columns):
    clean_columns = {}
    raw_columns = {}
    profiles = []
    conversion_errors = {}
    unusable_columns = 0
    row_count = len(df.index)

    for column in mapped_columns:
        normalized = column.get("normalized_name")
        if not column.get("include", True) or column.get("semantic_role") == "Ignored":
            unusable_columns += 1
            continue
        if normalized not in df.columns:
            unusable_columns += 1
            conversion_errors[normalized] = row_count
            continue

        detected_type = column.get("detected_type")
        role = column.get("semantic_role")
        raw_series = clean_series(df[normalized])
        converted, errors = convert_series(raw_series, detected_type, role)
        clean_columns[normalized] = converted
        raw_columns[normalized] = raw_series
        conversion_errors[normalized] = errors
        profiles.append(make_dataset_profile(column, converted, errors))

    if not clean_columns:
        frappe.throw(_("Aucune colonne exploitable après mapping."))
    clean_df = pd.DataFrame(clean_columns)
    raw_for_tables = pd.DataFrame(raw_columns)
    return clean_df, raw_for_tables, profiles, conversion_errors, unusable_columns
```

```python
def convert_series(series, detected_type, role):
    non_null_count = int(series.notna().sum())
    if role == "Measure":
        converted = parse_number_series(series)
        errors = int((series.notna() & converted.isna()).sum())
        return converted, errors
    if role == "Date Dimension":
        dates = parse_date_series(series)
        errors = int((series.notna() & dates.isna()).sum())
        return dates.dt.date.where(pd.notnull(dates), None), errors
    if detected_type == "Boolean":
        booleans = parse_boolean_series(series)
        errors = int((series.notna() & booleans.isna()).sum())
        return booleans.map(lambda value: "Yes" if value is True else "No" if value is False else None), errors
    if role == "Dimension":
        return series.map(normalize_category_value), 0
    converted = series.map(lambda value: str(value).strip() if value is not None else None)
    return converted, 0 if non_null_count else 0
```

### Load manuel : création du BI Dataset et des tables analytiques

Fichier : `api/etl.py`

```python
@frappe.whitelist()
def start_transform(dataset_import_name):
    dataset_import = ensure_dataset_import_access(dataset_import_name, write=True)
    require_validated_mapping(dataset_import.name)
    dataset_import.status = "Transforming"
    dataset_import.error_message = None
    dataset_import.save(ignore_permissions=True)
    create_job_log(dataset_import.name, "Transform", "Started", _("Transformation mise en file d'attente."))
    frappe.enqueue(
        "bi_studio.api.etl.transform_dataset_job",
        queue="long",
        timeout=1200,
        enqueue_after_commit=True,
        dataset_import_name=dataset_import.name,
    )
    return {"queued": True, "dataset_import": dataset_import.name}
```

```python
@frappe.whitelist()
def transform_dataset_job(dataset_import_name):
    dataset_import = frappe.get_doc("BI Dataset Import", dataset_import_name)
    log = create_job_log(dataset_import.name, "Transform", "Started", _("Transformation démarrée."))
    dataset = None
    try:
        file_doc, raw_df = read_import_dataframe(dataset_import)
        normalized_df, original_columns = normalize_dataframe_columns(raw_df)
        mapping_doc = require_validated_mapping(dataset_import.name)
        mapping = from_json(mapping_doc.mapping_json, {})
        mapped_columns = mapping.get("columns") or []
        normalized_df, rule_warnings = apply_transform_rules(dataset_import.name, normalized_df)
        clean_df, raw_for_tables, column_profiles, conversion_errors, unusable_columns = build_clean_dataframe(
            normalized_df, mapped_columns
        )
        quality = calculate_quality_score(clean_df, conversion_errors, unusable_columns)
        dataset_title = unique_dataset_name(dataset_import.dataset_name or "Dataset")

        dataset = frappe.get_doc(
            {
                "doctype": "BI Dataset",
                "dataset_name": dataset_title,
                "description": _("Dataset transformé depuis {0}").format(dataset_import.name),
                "status": "Processing",
                "source_file": dataset_import.import_file,
                "source_import": dataset_import.name,
                "row_count": len(clean_df.index),
                "column_count": len(clean_df.columns),
                "quality_score": quality["score"],
                "imported_at": now_datetime(),
                "last_transformed_on": now_datetime(),
                "clean_data_json": to_json(dataframe_to_json_rows(clean_df)),
                "schema_json": to_json(
                    {
                        "source_import": dataset_import.name,
                        "mapping": mapping,
                        "columns": [profile_to_schema(profile) for profile in column_profiles],
                        "quality": quality,
                        "rule_warnings": rule_warnings,
                    }
                ),
            }
        )
        append_dataset_columns(dataset, column_profiles)
        dataset.insert(ignore_permissions=True)

        create_analytical_tables(dataset, raw_for_tables, clean_df, [profile.compatible_profile for profile in column_profiles])
        dataset.status = "Ready"
        dataset.save(ignore_permissions=True)

        dataset_import.status = "Ready"
        dataset_import.import_log = append_log(
            dataset_import.import_log,
            _("Transformation terminée. Dataset {0}, qualité {1}/100.").format(dataset.name, quality["score"]),
        )
        dataset_import.save(ignore_permissions=True)

        update_job_log(log, "Success", _("Transformation terminée."))
        return {"dataset": dataset.name, "quality_score": quality["score"], "dataset_import": dataset_import.name}
    except Exception as exc:
        dataset_import.status = "Failed"
        dataset_import.error_message = str(exc)[:1000]
        dataset_import.import_log = append_log(dataset_import.import_log, _("Erreur transformation: {0}").format(str(exc)))
        dataset_import.save(ignore_permissions=True)
        if dataset:
            dataset.status = "Failed"
            dataset.save(ignore_permissions=True)
        update_job_log(log, "Failed", str(exc), traceback.format_exc())
        frappe.log_error(traceback.format_exc(), "BI Studio transform failed")
        raise
```

La création physique des tables analytiques est déléguée à `bi_studio.services.warehouse.create_analytical_tables`, donc l'implémentation détaillée de ces tables est hors du dossier `api`.

---

## Statuts et logs

Le pipeline automatique expose `get_pipeline_status` pour le frontend.

Fichier : `api/excel_pipeline.py`

```python
@frappe.whitelist()
def get_pipeline_status(import_name: str) -> dict[str, Any]:
    """Return the current pipeline state + recent ETL logs for the UI."""
    if not import_name:
        frappe.throw("Import name requis.")
    doc = frappe.get_doc("BI Dataset Import", import_name)
    logs = frappe.get_all(
        "BI ETL Job Log",
        filters={"dataset_import": import_name},
        fields=["name", "job_type", "status", "started_on", "finished_on", "log_message"],
        order_by="creation asc",
        limit=200,
    )
    return {
        "name": doc.name,
        "status": doc.status,
        "dataset_title": doc.dataset_title,
        "row_count_raw": doc.row_count_raw,
        "row_count_cleaned": doc.row_count_cleaned,
        "column_count_raw": doc.column_count_raw,
        "column_count_cleaned": doc.column_count_cleaned,
        "clean_dataset": doc.clean_dataset,
        "ai_dashboard_spec": doc.ai_dashboard_spec,
        "created_dashboard": doc.created_dashboard,
        "error_message": doc.error_message,
        "logs": logs,
    }
```

Le helper `log_step` crée une entrée **BI ETL Job Log** par étape :

```python
def log_step(
    import_name: str,
    step: str,
    status: str,
    message: str = "",
    traceback_text: str = "",
    started_on: datetime | None = None,
) -> None:
    """Create a BI ETL Job Log entry. Best-effort: never raise."""
    try:
        doc = frappe.new_doc("BI ETL Job Log")
        doc.dataset_import = import_name
        doc.job_type = step
        doc.status = status
        doc.started_on = started_on or now_datetime()
        if status in {"Success", "Failed"}:
            doc.finished_on = now_datetime()
        doc.log_message = (message or "")[:5000]
        doc.error_traceback = (traceback_text or "")[:5000]
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="BI Pipeline: log_step a échoué",
            message=traceback.format_exc(),
        )
```

---

## Résumé des responsabilités par fichier

| Fichier | Responsabilité |
| --- | --- |
| `api/excel_pipeline.py` | Orchestration moderne du pipeline, extraction Excel, persistance `BI Clean Dataset`, statut et logs |
| `api/etl_cleaning.py` | Nettoyage des données, parsing numérique/date, détection doublons/constantes/erreurs |
| `api/column_renamer.py` | Normalisation des noms de colonnes et labels français |
| `api/dataset_profiler.py` | Profilage des colonnes et génération du schéma compact |
| `api/cohere_dashboard.py` | Construction du prompt et persistance de la spec IA/fallback |
| `api/dashboard_builder.py` | Construction des documents dashboard à partir de la spec validée |
| `api/importer.py` | Ancien flux d'import et preview Excel |
| `api/profiling.py` | Ancien flux de profiling et mapping initial |
| `api/etl.py` | Ancien flux transform/load vers `BI Dataset` et tables analytiques |
| `api/dataset.py` | Endpoints legacy, marqués dépréciés pour l'import |

---

## Points importants

- Le pipeline recommandé est `bi_studio.api.excel_pipeline.run_excel_to_dashboard_pipeline`.
- Le dataset nettoyé moderne est stocké dans **BI Clean Dataset**.
- L'ancien flux `api/etl.py` stocke plutôt dans **BI Dataset** et crée les tables analytiques via un service externe au dossier `api`.
- Les transformations sont auditables via **BI ETL Job Log**.
- Les erreurs par étape basculent `BI Dataset Import.status` à `Failed` avec `error_message`.
- Le dashboard IA est une étape aval du pipeline, après ETL.
