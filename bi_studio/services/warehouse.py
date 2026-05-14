import datetime
from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import now_datetime

from bi_studio.services.naming import dimension_table_name, quote_identifier, table_name
from bi_studio.services.serialization import to_json


def _sql_type(data_type):
    # LONGTEXT par défaut : évite toute troncature silencieuse sur les textes longs
    if data_type == "Number":
        return "DOUBLE"
    if data_type == "Date":
        return "DATE"
    return "LONGTEXT"


def _normalise_value(value):
    # float NaN est la seule valeur Python où value != value (IEEE 754) ; pas de math.isnan pour rester générique
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    # pandas retourne parfois datetime.datetime même quand on demande .dt.date ; on force la date seule
    if isinstance(value, datetime.datetime):
        return value.date()
    return value


def _drop_table(name):
    frappe.db.sql_ddl(f"DROP TABLE IF EXISTS {quote_identifier(name)}")


def analytical_table_exists(name):
    # cached=False obligatoire : le cache de Frappe ne reflète pas les tables créées dans la même transaction
    return bool(name) and name in frappe.db.get_tables(cached=False)


def ensure_clean_table_exists(dataset_doc):
    if not analytical_table_exists(dataset_doc.clean_table):
        frappe.throw(
            _(
                "Les données préparées de ce dataset sont introuvables. "
                "Supprimez ce dataset puis réimportez le fichier Excel."
            )
        )


def _create_raw_table(table, columns):

    parts = ["`id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY"]
    parts.extend(f"{quote_identifier(column)} LONGTEXT" for column in columns)
    frappe.db.sql_ddl(f"CREATE TABLE {quote_identifier(table)} ({', '.join(parts)}) ENGINE=InnoDB")


def _create_clean_table(table, profiles):
    parts = ["`id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY"]
    parts.extend(f"{quote_identifier(profile.name)} {_sql_type(profile.data_type)}" for profile in profiles)
    frappe.db.sql_ddl(f"CREATE TABLE {quote_identifier(table)} ({', '.join(parts)}) ENGINE=InnoDB")


def _bulk_insert(table, columns, rows):
    if not rows:
        return
    fields = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    # Découpage en tranches de 200 lignes : au-delà, la requête dépasse régulièrement
    # la limite max_allowed_packet de MariaDB en configuration par défaut (1 Mo).
    for start in range(0, len(rows), 200):
        chunk = rows[start : start + 200]
        values_sql = ", ".join(f"({placeholders})" for _ in chunk)
        flat_values = [value for row in chunk for value in row]
        sql = f"INSERT INTO {quote_identifier(table)} ({fields}) VALUES {values_sql}"
        frappe.db.sql(sql, flat_values)


def _rows_from_dataframe(df, columns):
    rows = []
    for _, row in df.iterrows():
        rows.append(tuple(_normalise_value(row[column]) for column in columns))
    return rows


def create_analytical_tables(dataset_doc, raw_df, clean_df, profiles):
    raw_table = table_name("raw", dataset_doc.name)
    clean_table = table_name("clean", dataset_doc.name)
    fact_table = table_name("fact", dataset_doc.name)

    # DROP avant CREATE : rend la fonction idempotente en cas de ré-import sur un dataset existant
    for table in [raw_table, clean_table, fact_table]:
        _drop_table(table)

    profile_by_name = {profile.name: profile for profile in profiles}
    columns = [profile.name for profile in profiles]

    # raw_copy reçoit les noms de colonnes scrubbed (snake_case) tout en gardant les valeurs brutes d'origine
    raw_copy = raw_df.copy()
    raw_copy.columns = columns
    _create_raw_table(raw_table, columns)
    _bulk_insert(raw_table, columns, _rows_from_dataframe(raw_copy, columns))

    _create_clean_table(clean_table, profiles)
    _bulk_insert(clean_table, columns, _rows_from_dataframe(clean_df, columns))

    dimensions = create_dimension_tables(dataset_doc, clean_df, profiles)
    create_fact_table(fact_table, clean_df, profiles, dimensions)

    model = {
        "raw_table": raw_table,
        "clean_table": clean_table,
        "fact_table": fact_table,
        "dimensions": dimensions,
        "columns": [
            {
                "name": profile.name,
                "label": profile.label,
                "type": profile.data_type,
                "is_numeric": profile.is_numeric,
                "is_category": profile.is_category,
                "is_date": profile.is_date,
            }
            for profile in profiles
        ],
    }

    dataset_doc.raw_table = raw_table
    dataset_doc.clean_table = clean_table
    dataset_doc.fact_table = fact_table
    dataset_doc.warehouse_model_json = to_json(model)

    sync_warehouse_docs(dataset_doc, model, profile_by_name, len(clean_df.index))
    return model


def create_dimension_tables(dataset_doc, clean_df, profiles):
    dimensions = []
    for profile in profiles:
        if not profile.is_category and not profile.is_date:
            continue

        dim_table = dimension_table_name(profile.name, dataset_doc.name)
        _drop_table(dim_table)
        if profile.is_date:
            frappe.db.sql_ddl(
                f"""CREATE TABLE {quote_identifier(dim_table)} (
                    `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    `value` DATE,
                    `year` INT,
                    `month` INT,
                    `day` INT
                ) ENGINE=InnoDB"""
            )
            unique_values = sorted(value for value in clean_df[profile.name].dropna().unique())
            # Décomposition en Python plutôt qu'en SQL : évite YEAR()/MONTH() qui varient selon le moteur
            rows = [(value, value.year, value.month, value.day) for value in unique_values]
            _bulk_insert(dim_table, ["value", "year", "month", "day"], rows)
        else:
            frappe.db.sql_ddl(
                f"""CREATE TABLE {quote_identifier(dim_table)} (
                    `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    `value` VARCHAR(255)
                ) ENGINE=InnoDB"""
            )
            unique_values = clean_df[profile.name].dropna().astype(str).drop_duplicates().sort_values().tolist()
            # Troncature à 255 caractères pour rester dans la limite VARCHAR ; les valeurs plus longues
            # sont rares dans les colonnes catégorielles et le profiling impose déjà distinct_count ≤ 100.
            rows = [(value[:255],) for value in unique_values]
            _bulk_insert(dim_table, ["value"], rows)

        dimensions.append(
            {
                "table": dim_table,
                "source_column": profile.name,
                "label": profile.label,
                "type": "Date" if profile.is_date else "Category",
                "row_count": len(rows),
            }
        )
    return dimensions


def create_fact_table(fact_table, clean_df, profiles, dimensions):
    dimension_by_column = {dimension["source_column"]: dimension for dimension in dimensions}
    # OrderedDict garantit un ordre stable des colonnes entre la définition DDL et l'insertion
    fields = OrderedDict()
    fields["source_row_id"] = "INT"

    for profile in profiles:
        if profile.is_numeric:
            # Mesure directe : la valeur numérique brute va dans la table de faits
            fields[profile.name] = "DOUBLE"
        elif profile.name in dimension_by_column:
            # Clé étrangère vers la dimension correspondante ; suffixe _id par convention
            fields[f"{profile.name}_id"] = "INT"

    ddl = ["`id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY"]
    ddl.extend(f"{quote_identifier(field)} {field_type}" for field, field_type in fields.items())
    frappe.db.sql_ddl(f"CREATE TABLE {quote_identifier(fact_table)} ({', '.join(ddl)}) ENGINE=InnoDB")

    # Chargement de toutes les dimensions en mémoire avant l'insertion : évite N requêtes SELECT
    # par ligne. Viable car le volume max est 1 000 lignes et les dimensions ont une faible cardinalité.
    dimension_maps = {}
    for dimension in dimensions:
        rows = frappe.db.sql(f"SELECT id, value FROM {quote_identifier(dimension['table'])}", as_dict=True)
        dimension_maps[dimension["source_column"]] = {str(row.value): row.id for row in rows}

    insert_rows = []
    for index, row in clean_df.iterrows():
        values = []
        for field in fields:
            if field == "source_row_id":
                # index + 1 car les id dans bi_clean commencent à 1 (AUTO_INCREMENT)
                values.append(index + 1)
                continue
            # Retrouve le nom de colonne source depuis le nom du champ (retire le suffixe _id si FK)
            source_column = field[:-3] if field.endswith("_id") else field
            value = _normalise_value(row.get(source_column))
            if field.endswith("_id"):
                # Résolution de la FK : str(value) pour correspondre aux clés string du dictionnaire
                values.append(dimension_maps.get(source_column, {}).get(str(value)))
            else:
                values.append(value)
        insert_rows.append(tuple(values))

    _bulk_insert(fact_table, list(fields.keys()), insert_rows)


def sync_warehouse_docs(dataset_doc, model, profile_by_name, row_count):
    # Suppression préalable : un re-import sur le même dataset recrée les tables SQL mais aussi
    # les DocTypes de métadonnées ; on évite les doublons en vidant d'abord les anciens enregistrements.
    for doctype in ["BI Warehouse Model", "BI Fact Table", "BI Dimension Table"]:
        for name in frappe.get_all(doctype, filters={"dataset": dataset_doc.name}, pluck="name"):
            frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)

    frappe.get_doc(
        {
            "doctype": "BI Warehouse Model",
            "dataset": dataset_doc.name,
            "raw_table": model["raw_table"],
            "clean_table": model["clean_table"],
            "fact_table": model["fact_table"],
            "model_json": to_json(model),
            "generated_at": now_datetime(),
        }
    ).insert(ignore_permissions=True)

    numeric_columns = [profile.name for profile in profile_by_name.values() if profile.is_numeric]
    date_columns = [profile.name for profile in profile_by_name.values() if profile.is_date]
    category_columns = [profile.name for profile in profile_by_name.values() if profile.is_category]
    frappe.get_doc(
        {
            "doctype": "BI Fact Table",
            "dataset": dataset_doc.name,
            "table_name": model["fact_table"],
            "numeric_columns_json": to_json(numeric_columns),
            "date_columns_json": to_json(date_columns),
            "category_columns_json": to_json(category_columns),
            "row_count": row_count,
        }
    ).insert(ignore_permissions=True)

    for dimension in model["dimensions"]:
        frappe.get_doc(
            {
                "doctype": "BI Dimension Table",
                "dataset": dataset_doc.name,
                "table_name": dimension["table"],
                "dimension_type": dimension["type"],
                "source_column": dimension["source_column"],
                "row_count": dimension["row_count"],
            }
        ).insert(ignore_permissions=True)


def drop_dataset_tables(dataset_doc):
    tables = [dataset_doc.raw_table, dataset_doc.clean_table, dataset_doc.fact_table]
    dimensions = frappe.get_all("BI Dimension Table", filters={"dataset": dataset_doc.name}, pluck="table_name")
    for table in [*tables, *dimensions]:
        if table:
            _drop_table(table)
