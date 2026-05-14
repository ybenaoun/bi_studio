import pandas as pd


def calculate_quality_score(df, conversion_errors=None, unusable_columns=0):
    conversion_errors = conversion_errors or {}
    total_cells = max(int(df.shape[0] * df.shape[1]), 1)
    missing_cells = int(df.isna().sum().sum()) if not df.empty else 0
    missing_rate = missing_cells / total_cells
    duplicate_rate = float(df.duplicated().sum() / len(df.index)) if len(df.index) else 0
    conversion_error_count = sum(int(value or 0) for value in conversion_errors.values())
    conversion_error_rate = conversion_error_count / total_cells
    unusable_rate = float(unusable_columns / max(int(df.shape[1]), 1))

    score = 100
    score -= missing_rate * 35
    score -= duplicate_rate * 25
    score -= conversion_error_rate * 30
    score -= unusable_rate * 20
    score = max(0, min(100, round(score, 2)))

    return {
        "score": score,
        "missing_rate": round(missing_rate, 4),
        "duplicate_rate": round(duplicate_rate, 4),
        "conversion_error_rate": round(conversion_error_rate, 4),
        "conversion_errors": conversion_errors,
        "unusable_columns": int(unusable_columns or 0),
    }


def dataframe_to_json_rows(df):
    safe_df = df.copy().where(pd.notnull(df), None)
    return safe_df.to_dict(orient="records")

