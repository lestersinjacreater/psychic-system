import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="Bank Name Cleaner",
    page_icon="🏦",
    layout="wide",
)


BASE_DIR = Path(__file__).resolve().parent
JSON_DIRECTORY_PATH = BASE_DIR / "bic_directory.json"
CSV_DIRECTORY_PATH = BASE_DIR / "bic_directory.csv"
DIRECTORY_PATH = JSON_DIRECTORY_PATH if JSON_DIRECTORY_PATH.exists() else CSV_DIRECTORY_PATH
BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
PUNCTUATION_RE = re.compile(r"[.,\-–—/\\&()'+]+")
WHITESPACE_RE = re.compile(r"\s+")
REMOVABLE_WORDS = {
    "BANK",
    "BANQUE",
    "BANCO",
    "NA",
    "N",
    "A",
    "PLC",
    "LTD",
    "LIMITED",
    "AG",
    "SA",
    "NV",
    "INC",
    "CORP",
    "CORPORATION",
    "THE",
    "CO",
    "COMPANY",
    "GROUP",
    "HOLDINGS",
    "HOLDING",
    "INTERNATIONAL",
    "INTL",
    "BRANCH",
}

BANK_NAME_HEADERS = {
    "bank_name",
    "bankname",
    "bank name",
    "name",
    "institution",
}
SWIFT_HEADERS = {
    "eft_swit_code",
    "eft_swift_code",
    "swift",
    "swift_code",
    "swift code",
    "bic",
    "bic_code",
    "code",
}


if "lookup_cache" not in st.session_state:
    st.session_state.lookup_cache = {}
if "lookup_sources" not in st.session_state:
    st.session_state.lookup_sources = {}
if "api_lookup_log" not in st.session_state:
    st.session_state.api_lookup_log = []


def _normalise_directory_code(value: object) -> str:
    return str(value).strip().upper().replace(" ", "")[:8]


def _normalise_directory_name(value: object) -> str:
    return str(value or "").strip()


def _extract_lookup_from_json_object(obj: object) -> dict[str, str]:
    lookup: dict[str, str] = {}
    if not isinstance(obj, dict):
        return lookup

    if "swift" in obj and "official_bank_name" in obj:
        code = _normalise_directory_code(obj.get("swift"))
        name = _normalise_directory_name(obj.get("official_bank_name"))
        if len(code) == 8 and name:
            lookup[code] = name
        return lookup

    if "properties" in obj and isinstance(obj["properties"], dict):
        props = obj["properties"]
        names = props.get("name")
        bics = props.get("swiftBic")
        if isinstance(names, list):
            name_value = names[0] if names else ""
        else:
            name_value = names or ""
        if isinstance(bics, list):
            code_values = bics
        elif bics is not None:
            code_values = [bics]
        else:
            code_values = []
        for code in code_values:
            normalised = _normalise_directory_code(code)
            if len(normalised) == 8 and name_value:
                lookup[normalised] = _normalise_directory_name(name_value)
        return lookup

    for key, value in obj.items():
        if isinstance(value, (str, int, float)):
            code = _normalise_directory_code(key)
            name = _normalise_directory_name(value)
            if len(code) == 8 and name:
                lookup[code] = name
    return lookup


def _read_json_directory(file_path: Path) -> dict[str, str]:
    text = file_path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return {}

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
        payload = records

    lookup: dict[str, str] = {}
    if isinstance(payload, dict):
        lookup.update(_extract_lookup_from_json_object(payload))
    elif isinstance(payload, list):
        for item in payload:
            lookup.update(_extract_lookup_from_json_object(item))
    else:
        raise ValueError(f"Unsupported JSON directory format in {file_path.name}")

    return lookup


@st.cache_data(show_spinner=False)
def load_bic_directory(path: str) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.exists():
        return {}

    if file_path.suffix.lower() in {".json", ".ndjson"}:
        return _read_json_directory(file_path)

    try:
        df = pd.read_csv(file_path, dtype=str).fillna("")
    except pd.errors.ParserError:
        return _read_json_directory(file_path)

    required = {"swift", "official_bank_name"}
    if not required.issubset(set(df.columns)):
        raise ValueError(
            "bic_directory.csv must contain columns: swift, official_bank_name"
        )

    lookup = {}
    for _, row in df.iterrows():
        code = _normalise_directory_code(row["swift"])
        name = _normalise_directory_name(row["official_bank_name"])
        if len(code) == 8 and name:
            lookup[code] = name
    return lookup


def normalise_header(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(value).strip().lower())


def header_key(value: str) -> str:
    return normalise_header(value).replace("_", "")


def find_column(columns, candidates: set[str]) -> str | None:
    candidate_keys = {header_key(item) for item in candidates}
    for column in columns:
        if header_key(str(column)) in candidate_keys:
            return column
        if normalise_header(str(column)) in candidates:
            return column
    return None


def read_upload(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, dtype=str, keep_default_na=False)
    return pd.read_excel(uploaded_file, dtype=str).fillna("")


def split_lines(text: str) -> list[str]:
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return [line.strip() for line in lines]


def pad_to_length(values: list[str], length: int) -> list[str]:
    padded = list(values)
    if len(padded) < length:
        padded.extend([""] * (length - len(padded)))
    return padded[:length]


def names_are_same(left: str, right: str) -> bool:
    def tokens(value: str) -> set[str]:
        text = PUNCTUATION_RE.sub(" ", (value or "").upper())
        text = WHITESPACE_RE.sub(" ", text).strip()
        return {part for part in text.split(" ") if part and part not in REMOVABLE_WORDS}

    left_tokens = tokens(left)
    right_tokens = tokens(right)
    if not left_tokens and not right_tokens:
        return True
    if not left_tokens or not right_tokens:
        return False
    return left_tokens == right_tokens


def normalise_swift(value: str) -> tuple[str, str, str]:
    cleaned = "" if value is None else str(value).strip().upper().replace(" ", "")
    if not cleaned:
        return "", "", "EMPTY"
    if not BIC_RE.match(cleaned):
        return cleaned, "", "INVALID"
    return cleaned, cleaned[:8], "OK"


def get_isvalid_api_key() -> str:
    try:
        value = st.secrets.get("IS_VALID_API_KEY", "") or st.secrets.get("IS_VALID_KEY", "")
    except Exception:
        value = ""
    return str(value or "").strip()


def lookup_official_name_via_api(institution_key: str) -> str:
    api_key = get_isvalid_api_key()
    if not api_key or len(institution_key) != 8:
        return ""

    url = f"https://api.isvalid.dev/v0/bic?value={institution_key}"
    lookup_result = ""
    status_code = None
    success = False
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        status_code = response.status_code
        if response.status_code != 200:
            lookup_result = ""
        else:
            payload = response.json()
            if isinstance(payload, list):
                payload = payload[0] if payload else {}

            if isinstance(payload, dict):
                for key in ("data", "result", "bank"):
                    candidate = payload.get(key)
                    if isinstance(candidate, dict):
                        payload = candidate
                        break

            if not isinstance(payload, dict):
                lookup_result = ""
            else:
                for key in ("official_bank_name", "bank_name", "name", "institution", "bank"):
                    value = payload.get(key)
                    if value:
                        lookup_result = str(value).strip()
                        success = True
                        break

                if not success:
                    for nested in ("bank", "institution"):
                        value = payload.get(nested)
                        if isinstance(value, dict):
                            for key in ("name", "official_bank_name", "bank_name"):
                                nested_value = value.get(key)
                                if nested_value:
                                    lookup_result = str(nested_value).strip()
                                    success = True
                                    break
                            if success:
                                break
    except Exception:
        lookup_result = ""
        status_code = None
        success = False
    finally:
        st.session_state.api_lookup_log = st.session_state.get("api_lookup_log", [])
        st.session_state.api_lookup_log.append(
            {
                "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "bic": institution_key,
                "status_code": status_code,
                "success": success,
                "result": lookup_result,
            }
        )

    return lookup_result


def lookup_official_name(institution_key: str, directory: dict[str, str]) -> tuple[str, str]:
    cached = st.session_state.lookup_cache.get(institution_key)
    if cached is not None:
        source = st.session_state.lookup_sources.get(institution_key, "local_directory")
        return cached, source

    name = directory.get(institution_key, "")
    source = "local_directory" if name else "api"
    if not name:
        name = lookup_official_name_via_api(institution_key)
        if not name:
            source = "api_not_found"

    st.session_state.lookup_cache[institution_key] = name
    st.session_state.lookup_sources[institution_key] = source
    return name, source


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


try:
    bic_directory = load_bic_directory(str(DIRECTORY_PATH))
    directory_error = ""
except Exception as exc:
    bic_directory = {}
    directory_error = str(exc)


st.title("🏦 Bank Name Cleaner")
st.markdown(
    """
    Compare each **bank name** with the official name for its **SWIFT / BIC**.

    The SWIFT code is the source of truth.
    Empty cells stay empty and stay on the same row.
    Other sheet columns are passed through unchanged.
    """
)

st.sidebar.header("Directory")
st.sidebar.write(f"Loaded codes: {len(bic_directory):,}")
if directory_error:
    st.sidebar.error(directory_error)
elif not DIRECTORY_PATH.exists():
    st.sidebar.error("bic_directory.csv was not found next to app.py")

test_swift = st.sidebar.text_input("Test a SWIFT")
if st.sidebar.button("Lookup"):
    key = test_swift.strip().upper().replace(" ", "")[:8]
    st.sidebar.write(bic_directory.get(key) or "Not in directory")


st.header("1. Load data")
input_mode = st.radio(
    "Input method",
    ["Upload file", "Paste bank_name and eft_swit_code separately"],
    horizontal=True,
)

input_df = None
bank_col = None
swift_col = None
error = ""

if input_mode == "Upload file":
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])
    if uploaded is not None:
        try:
            input_df = read_upload(uploaded)
            bank_col = find_column(input_df.columns, BANK_NAME_HEADERS)
            swift_col = find_column(input_df.columns, SWIFT_HEADERS)
            if bank_col is None or swift_col is None:
                error = (
                    "Could not find bank_name and eft_swit_code columns. "
                    f"Found columns: {list(input_df.columns)}"
                )
        except Exception as exc:
            error = str(exc)
else:
    left, right = st.columns(2)
    with left:
        bank_paste = st.text_area("bank_name column", height=240)
    with right:
        swift_paste = st.text_area("eft_swit_code column", height=240)
    if bank_paste.strip() or swift_paste.strip():
        banks = split_lines(bank_paste)
        swifts = split_lines(swift_paste)
        if banks and header_key(banks[0]) in {header_key(item) for item in BANK_NAME_HEADERS}:
            banks = banks[1:]
        if swifts and header_key(swifts[0]) in {header_key(item) for item in SWIFT_HEADERS}:
            swifts = swifts[1:]
        length = max(len(banks), len(swifts))
        if length == 0:
            error = "Both pasted columns are empty."
        else:
            input_df = pd.DataFrame(
                {
                    "bank_name": pad_to_length(banks, length),
                    "eft_swit_code": pad_to_length(swifts, length),
                }
            )
            bank_col = "bank_name"
            swift_col = "eft_swit_code"

if error:
    st.error(error)
    st.stop()

if input_df is None:
    st.info("Upload a sheet or paste the two columns to continue.")
    st.stop()

if not bic_directory and not get_isvalid_api_key():
    st.error("The local BIC directory is empty and no IsValid API key is configured.")
    st.stop()

input_df = input_df.copy()
input_df.insert(0, "row_number", range(1, len(input_df) + 1))
st.caption(f"Using columns: `{bank_col}` and `{swift_col}`")
st.dataframe(input_df, use_container_width=True, hide_index=True)

if not st.button("Clean bank names", type="primary"):
    st.stop()

st.session_state.api_lookup_log = []
st.session_state.lookup_cache = {}
st.session_state.lookup_sources = {}
rows = []
changes = []
started = datetime.utcnow()
progress = st.progress(0.0)

for index, record in input_df.iterrows():
    original_name = "" if pd.isna(record[bank_col]) else str(record[bank_col]).strip()
    raw_swift = "" if pd.isna(record[swift_col]) else str(record[swift_col])
    cleaned_swift, institution_key, swift_state = normalise_swift(raw_swift)

    official_name = ""
    final_name = original_name
    status = ""
    source = ""

    if swift_state == "EMPTY":
        status = "EMPTY_SWIFT"
    elif swift_state == "INVALID":
        status = "INVALID_SWIFT"
    else:
        official_name, source = lookup_official_name(institution_key, bic_directory)
        if official_name:
            if not original_name:
                final_name = official_name
                status = "FILLED"
            elif names_are_same(original_name, official_name):
                final_name = original_name
                status = "CORRECT"
            else:
                final_name = official_name
                status = "CORRECTED"
                changes.append(
                    {
                        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                        "row_number": int(record["row_number"]),
                        "original_bank_name": original_name,
                        "final_bank_name": final_name,
                        "eft_swit_code": cleaned_swift,
                    }
                )
        else:
            status = "NOT IN DIRECTORY"

    row_out = record.to_dict()
    row_out["original_bank_name"] = original_name
    row_out["final_bank_name"] = final_name
    row_out["official_bank_name"] = official_name
    row_out["normalised_swift"] = cleaned_swift
    row_out["official_swift"] = institution_key
    row_out["status"] = status
    row_out["source"] = source
    row_out[bank_col] = final_name
    if cleaned_swift:
        row_out[swift_col] = cleaned_swift
    rows.append(row_out)
    progress.progress((index + 1) / len(input_df))

output_df = pd.DataFrame(rows)
change_log_df = pd.DataFrame(changes)
elapsed = (datetime.utcnow() - started).total_seconds()

st.header("2. Summary")
api_lookup_entries = st.session_state.get("api_lookup_log", [])
api_lookup_count = len(api_lookup_entries)
api_lookup_success = sum(1 for entry in api_lookup_entries if entry.get("success"))
api_lookup_failures = api_lookup_count - api_lookup_success

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Rows", len(output_df))
c2.metric("Corrected", int((output_df["status"] == "CORRECTED").sum()))
c3.metric("Filled", int((output_df["status"] == "FILLED").sum()))
c4.metric("Already correct", int((output_df["status"] == "CORRECT").sum()))
c5.metric("Not in directory", int((output_df["status"] == "NOT IN DIRECTORY").sum()))
c6.metric("API lookups", api_lookup_count)

if api_lookup_count:
    st.caption(
        f"External API lookups attempted: {api_lookup_count}. "
        f"Successful responses: {api_lookup_success}. "
        f"Failed/empty responses: {api_lookup_failures}."
    )
else:
    st.caption("No external API lookups were required for this run.")

st.header("3. API log")
if api_lookup_entries:
    st.dataframe(
        pd.DataFrame(api_lookup_entries)[
            ["timestamp", "bic", "status_code", "success", "result"]
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No API calls were made during this processing run.")

st.header("4. Results")
st.dataframe(output_df, use_container_width=True, hide_index=True)

st.header("4. Change log")
if change_log_df.empty:
    st.info("No bank names were changed.")
else:
    st.dataframe(change_log_df, use_container_width=True, hide_index=True)

st.header("5. Download")
d1, d2, d3 = st.columns(3)
d1.download_button(
    "⬇️ Download cleaned sheet",
    data=dataframe_to_csv_bytes(output_df),
    file_name="cleaned_bank_data.csv",
    mime="text/csv",
    use_container_width=True,
)
d2.download_button(
    "⬇️ Download change log",
    data=dataframe_to_csv_bytes(
        change_log_df if not change_log_df.empty else pd.DataFrame(columns=["timestamp"])
    ),
    file_name="bank_name_change_log.csv",
    mime="text/csv",
    use_container_width=True,
)
d3.download_button(
    "⬇️ Download comparison only",
    data=dataframe_to_csv_bytes(
        output_df[
            [
                "row_number",
                "original_bank_name",
                "final_bank_name",
                "official_bank_name",
                "normalised_swift",
                "status",
            ]
        ]
    ),
    file_name="bank_name_comparison.csv",
    mime="text/csv",
    use_container_width=True,
)

st.caption(
    f"Processed {len(output_df)} rows against {len(bic_directory):,} directory codes "
    f"in {elapsed:.2f} seconds."
)

