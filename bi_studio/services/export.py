import base64
import io
import re

import frappe
import pandas as pd
from frappe import _
from frappe.utils.file_manager import save_file

from bi_studio.services.naming import quote_identifier
from bi_studio.services.permissions import ensure_dashboard_access, ensure_dataset_access
from bi_studio.services.query import get_dataset_columns
from bi_studio.services.warehouse import ensure_clean_table_exists


def export_clean_dataset_file(dataset_name):
    dataset = ensure_dataset_access(dataset_name)
    ensure_clean_table_exists(dataset)
    columns = get_dataset_columns(dataset.name)
    fields = [column["column_name"] for column in columns]
    labels = {column["column_name"]: column["column_label"] for column in columns}
    rows = []
    if fields:
        rows = frappe.db.sql(
            f"SELECT {', '.join(quote_identifier(field) for field in fields)} FROM {quote_identifier(dataset.clean_table)}",
            as_dict=True,
        )
    df = pd.DataFrame(rows)
    if fields:
        df = df[fields].rename(columns=labels)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Données nettoyées")

    file_doc = save_file(
        f"{dataset.dataset_name or dataset.name}.xlsx",
        output.getvalue(),
        "BI Dataset",
        dataset.name,
        is_private=1,
    )
    return {"file_url": file_doc.file_url, "file_name": file_doc.file_name}


def save_dashboard_png(dashboard_name, image_data):
    dashboard = ensure_dashboard_access(dashboard_name)
    if not image_data:
        frappe.throw(_("Aucune image PNG n'a été reçue."))

    match = re.match(r"^data:image/png;base64,(.+)$", image_data)
    if not match:
        frappe.throw(_("Le format d'export doit être PNG."))

    content = base64.b64decode(match.group(1))
    file_doc = save_file(
        f"{dashboard.dashboard_name or dashboard.name}.png",
        content,
        "BI Dashboard",
        dashboard.name,
        is_private=1,
    )
    return {"file_url": file_doc.file_url, "file_name": file_doc.file_name}
