import datetime
import json
from decimal import Decimal

import pandas as pd


def to_json(value):
    return json.dumps(value, ensure_ascii=False, default=json_default)


def from_json(value, fallback=None):
    if not value:
        return fallback if fallback is not None else {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback if fallback is not None else {}


def json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if pd.isna(value):
        return None
    return str(value)


def dataframe_preview(df, limit=20):
    preview_df = df.head(limit).copy()
    preview_df = preview_df.where(pd.notnull(preview_df), None)
    return from_json(to_json(preview_df.to_dict(orient="records")), [])

