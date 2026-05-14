import hashlib
import re
import unicodedata


SQL_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def scrub_label(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower())
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "column"
    if value[0].isdigit():
        value = f"c_{value}"
    return value[:54]


def dedupe_names(labels):
    seen = {}
    names = []
    for label in labels:
        base = scrub_label(label)
        count = seen.get(base, 0)
        seen[base] = count + 1
        names.append(base if count == 0 else f"{base}_{count + 1}")
    return names


def dataset_token(dataset_name):
    digest = hashlib.sha1(dataset_name.encode("utf-8")).hexdigest()[:10]
    return scrub_label(dataset_name)[:28] + "_" + digest


def table_name(prefix, dataset_name):
    return f"bi_{prefix}_{dataset_token(dataset_name)}"[:60]


def dimension_table_name(column_name, dataset_name):
    return f"bi_dim_{scrub_label(column_name)[:22]}_{dataset_token(dataset_name)[:24]}"[:60]


def quote_identifier(identifier):
    if not SQL_IDENTIFIER_PATTERN.match(identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier}")
    return f"`{identifier}`"

