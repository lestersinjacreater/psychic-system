import re
import time
from datetime import datetime
from difflib import SequenceMatcher      

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="Bank Name & SWIFT Data Cleaner",
    page_icon="🏦",
    layout="wide",
)


API_URL = "https://api.isvalid.dev/v0/bic"
BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
BANK_HEADER_RE = re.compile(
    r"^(bank|bank[_\s-]?name|name|beneficiary|institution)s?$",
    re.I,
)
SWIFT_HEADER_RE = re.compile(
    r"^(swift|swift[_\s-]?code|bic|bic[_\s-]?code|code)s?$",
    re.I,
)
REMOVABLE_WORDS = {
    "BANK", "BANQUE", "BANCO", "NA", "N", "A", "PLC", "LTD", "LIMITED",
    "AG", "SA", "NV", "INC", "CORP", "CORPORATION", "THE", "CO", "COMPANY",
    "GROUP", "HOLDINGS", "HOLDING", "INTERNATIONAL", "INTL", "BRANCH",
}
PUNCTUATION_RE = re.compile(r"[.,\-–—/\\&()'+]+")
WHITESPACE_RE = re.compile(r"\s+")


st.title("🏦 Bank Name & SWIFT Data Cleaner")
st.markdown(
    """
    Correct bank names using the official name returned for each SWIFT/BIC code.

    For every valid SWIFT code the app will:
    1. Look up the official bank name.
    2. Keep the current name if it already matches.
    3. Replace it with the official name if it does not.
    4. Record every replacement in a change log.

    Empty Excel cells are kept as empty cells so rows stay aligned.
    """
)
st.info("The official bank name from the SWIFT API is the source of truth.")


def load_api_keys() -> list[str]:
    keys: list[str] = []
    try:
        raw = st.secrets.get("IS_VALID_API_KEYS", [])
        if isinstance(raw, str):
            keys.extend(part.strip() for part in raw.replace(",", "\n").splitlines())
        elif raw:
            keys.extend(str(item).strip() for item in raw)
        single = st.secrets.get("IS_VALID_API_KEY", "")
        if single:
            keys.append(str(single).strip())
    except Exception:
        pass

    unique: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


API_KEYS = load_api_keys()
if not API_KEYS:
    st.error(
        "No API keys configured. Add `IS_VALID_API_KEYS` or `IS_VALID_API_KEY` "
        "to your Streamlit secrets."
    )
    st.stop()

if "lookup_success_cache" not in st.session_state:
    st.session_state.lookup_success_cache = {}
if "exhausted_keys" not in st.session_state:
    st.session_state.exhausted_keys = set()
if "results" not in st.session_state:
    st.session_state.results = None

available_keys = [key for key in API_KEYS if key not in st.session_state.exhausted_keys]
st.caption(f"{len(available_keys)} of {len(API_KEYS)} API key(s) available this session.")


def split_lines(text: str) -> list[str]:
    """
    Convert a pasted Excel column into cell values.

    Empty cells are kept. Only extra trailing newlines from the text
    area are removed, never blank cells in the middle of the column.
    """
    if text is None:
        return []

    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return [line.strip() for line in lines]


def looks_like_header(value: str, kind: str) -> bool:
    if not value:
        return False
    pattern = BANK_HEADER_RE if kind == "bank" else SWIFT_HEADER_RE
    return bool(pattern.match(value.strip()))


def remove_optional_header(values: list[str], kind: str) -> list[str]:
    if values and looks_like_header(values[0], kind):
        return values[1:]
    return values


def detect_separator(line: str) -> str | None:
    if "\t" in line:
        return "\t"
    if ";" in line:
        return ";"
    if "," in line:
        return ","
    return None


def parse_two_column_paste(text: str) -> pd.DataFrame | None:
    """
    Parse two Excel columns pasted together.

    Empty cells stay empty. A blank line is kept as a blank row.
    """
    if text is None:
        return None

    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None

    sep = detect_separator(lines[0])
    if sep is None:
        for line in lines:
            sep = detect_separator(line)
            if sep is not None:
                break
    if sep is None:
        return None

    rows = []
    for line in lines:
        parts = [part.strip() for part in line.split(sep)]
        if len(parts) == 1:
            parts.append("")
        rows.append([parts[0], parts[1]])

    header_bank, header_swift = rows[0]
    if looks_like_header(header_bank, "bank") or looks_like_header(header_swift, "swift"):
        data = rows[1:]
    else:
        data = rows

    return pd.DataFrame(data, columns=["original_bank_name", "swift"])


def clean_swift(raw: str) -> str:
    return (
        str(raw or "")
        .strip()
        .upper()
        .replace(" ", "")
        .replace("\t", "")
        .replace("-", "")
        .replace(".", "")
    )


def institution_key(swift: str) -> str:
    code = clean_swift(swift)
    return code[:8] if len(code) >= 8 else code


def normalise_swift(raw: str) -> dict:
    code = clean_swift(raw)
    if not code:
        return {
            "ok": False,
            "original": code,
            "lookup_code": "",
            "institution_key": "",
            "error": "Empty SWIFT code",
        }

    lookup_code = code + "XXX" if len(code) == 8 else code

    if len(code) not in (8, 11) or not re.match(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}", code):
        return {
            "ok": False,
            "original": code,
            "lookup_code": lookup_code,
            "institution_key": institution_key(code),
            "error": "SWIFT/BIC is not 8 or 11 alphanumeric characters in ISO 9362 format",
        }

    if len(code) == 11 and not re.match(r"^[A-Z0-9]{3}$", code[8:]):
        return {
            "ok": False,
            "original": code,
            "lookup_code": lookup_code,
            "institution_key": institution_key(code),
            "error": "SWIFT/BIC branch code is invalid",
        }

    if not BIC_RE.match(code) and not BIC_RE.match(lookup_code):
        return {
            "ok": False,
            "original": code,
            "lookup_code": lookup_code,
            "institution_key": institution_key(code),
            "error": "SWIFT/BIC is not 8 or 11 alphanumeric characters in ISO 9362 format",
        }

    return {
        "ok": True,
        "original": code,
        "lookup_code": lookup_code,
        "institution_key": institution_key(code),
        "error": "",
    }


def normalise_bank_name(name: str) -> str:
    if not name:
        return ""
    value = PUNCTUATION_RE.sub(" ", str(name).upper().strip())
    value = WHITESPACE_RE.sub(" ", value).strip()
    return " ".join(word for word in value.split() if word not in REMOVABLE_WORDS)


def names_are_same(supplied: str, official: str) -> bool:
    left = normalise_bank_name(supplied)
    right = normalise_bank_name(official)
    if not left and right:
        return False
    if left == right:
        return True
    if not left or not right:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.92


def parse_tabular_upload(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded, dtype=str, keep_default_na=False)
    if name.endswith(".tsv") or name.endswith(".txt"):
        return pd.read_csv(uploaded, sep="\t", dtype=str, keep_default_na=False)
    return pd.read_excel(uploaded, dtype=str).fillna("")


def guess_columns(columns: list[str]) -> tuple[str | None, str | None]:
    bank_col = None
    swift_col = None
    for col in columns:
        if bank_col is None and BANK_HEADER_RE.match(str(col).strip()):
            bank_col = col
        if swift_col is None and SWIFT_HEADER_RE.match(str(col).strip()):
            swift_col = col
    return bank_col, swift_col


def lookup_swift(code: str, retries_per_key: int = 2) -> dict:
    if not code:
        return {"valid": False, "error": "Empty SWIFT code", "retryable": False}

    keys = [key for key in API_KEYS if key not in st.session_state.exhausted_keys]
    if not keys:
        return {"valid": False, "error": "All API keys are rate limited", "retryable": True}

    last_error = "API request failed"
    retryable = True

    for key in keys:
        for attempt in range(retries_per_key + 1):
            try:
                response = requests.get(
                    API_URL,
                    params={"value": code},
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=20,
                )
            except requests.RequestException as error:
                last_error = f"API request failed: {error}"
                retryable = True
                if attempt < retries_per_key:
                    time.sleep(min(8.0, 1.0 * (2 ** attempt)))
                    continue
                break

            if response.status_code == 429:
                last_error = "API rate limit reached"
                retryable = True
                if attempt < retries_per_key:
                    retry_after = response.headers.get("Retry-After")
                    wait_s = (
                        float(retry_after)
                        if retry_after and str(retry_after).isdigit()
                        else min(4.0, 1.0 * (2 ** attempt))
                    )
                    time.sleep(wait_s)
                    continue
                st.session_state.exhausted_keys.add(key)
                break

            if response.status_code in {401, 403}:
                st.session_state.exhausted_keys.add(key)
                last_error = f"API authentication failed (HTTP {response.status_code})"
                retryable = False
                break

            if response.status_code in {500, 502, 503, 504}:
                last_error = f"API returned HTTP {response.status_code}"
                retryable = True
                if attempt < retries_per_key:
                    time.sleep(min(8.0, 1.0 * (2 ** attempt)))
                    continue
                break

            if response.status_code != 200:
                return {
                    "valid": False,
                    "error": f"API returned HTTP {response.status_code}",
                    "retryable": False,
                }

            try:
                data = response.json()
            except ValueError:
                return {"valid": False, "error": "API returned an invalid response", "retryable": False}

            if not data or not data.get("valid"):
                return {
                    "valid": False,
                    "error": "SWIFT/BIC code not found or not valid",
                    "retryable": False,
                }

            official_name = data.get("bankName") or data.get("bank_name") or data.get("name") or ""
            if not official_name:
                return {
                    "valid": False,
                    "error": "SWIFT/BIC is valid but no bank name was returned",
                    "retryable": False,
                }

            return {
                "valid": True,
                "official_bank_name": official_name,
                "official_swift": data.get("bic") or data.get("swift") or data.get("bankCode") or code,
                "city": data.get("city") or "",
                "country": data.get("countryName") or data.get("country") or "",
                "retryable": False,
            }

    return {"valid": False, "error": last_error, "retryable": retryable}


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


st.header("1. Load your data")
st.caption(
    "Paste directly from Excel. Blank cells are kept, so a missing bank name "
    "or SWIFT stays on that same row and does not shift the rows below it."
)

input_mode = st.radio(
    "Input method",
    ["Upload file", "Paste two columns together", "Paste columns separately"],
    horizontal=True,
)
input_df = None

if input_mode == "Upload file":
    uploaded = st.file_uploader(
        "Upload CSV, TSV, TXT, or Excel",
        type=["csv", "tsv", "txt", "xlsx", "xls"],
    )
    if uploaded is not None:
        try:
            raw_df = parse_tabular_upload(uploaded)
        except Exception as error:
            st.error(f"Could not read the file: {error}")
            raw_df = None

        if raw_df is not None and not raw_df.empty:
            raw_df = raw_df.fillna("")
            guessed_bank, guessed_swift = guess_columns(list(raw_df.columns))
            col_a, col_b = st.columns(2)
            bank_col = col_a.selectbox(
                "Bank name column",
                raw_df.columns,
                index=list(raw_df.columns).index(guessed_bank) if guessed_bank in raw_df.columns else 0,
            )
            swift_col = col_b.selectbox(
                "SWIFT / BIC column",
                raw_df.columns,
                index=(
                    list(raw_df.columns).index(guessed_swift)
                    if guessed_swift in raw_df.columns
                    else min(1, len(raw_df.columns) - 1)
                ),
            )
            input_df = pd.DataFrame(
                {
                    "original_bank_name": raw_df[bank_col].astype(str),
                    "swift": raw_df[swift_col].astype(str),
                }
            )

elif input_mode == "Paste two columns together":
    pasted_table = st.text_area(
        "Paste the Bank Name and SWIFT columns together from Excel",
        height=260,
        placeholder="Bank Name\tSWIFT\nJPMorgan Chase\tCHASUS33\n\tDEUTDEFF\nBarclays Bank Kenya\t",
    )
    parsed = parse_two_column_paste(pasted_table)
    if parsed is not None:
        input_df = parsed

else:
    st.markdown(
        "Paste each Excel column as-is, including blank cells. "
        "Row 1 of bank names is always matched with row 1 of SWIFT codes."
    )
    col1, col2 = st.columns(2)
    with col1:
        bank_names_raw = st.text_area("Bank names", height=280)
    with col2:
        swift_codes_raw = st.text_area("SWIFT codes", height=280)

    bank_names = remove_optional_header(split_lines(bank_names_raw), "bank")
    swift_codes = remove_optional_header(split_lines(swift_codes_raw), "swift")

    if bank_names or swift_codes:
        if len(bank_names) != len(swift_codes):
            st.warning(
                "The pasted columns have a different number of rows. "
                "Empty cells inside the columns were kept. "
                "Missing cells at the end will be added as blank."
            )
            st.caption(
                f"Bank names: **{len(bank_names)}** • SWIFT codes: **{len(swift_codes)}**"
            )
            target = max(len(bank_names), len(swift_codes))
            bank_names = bank_names + [""] * (target - len(bank_names))
            swift_codes = swift_codes + [""] * (target - len(swift_codes))

        input_df = pd.DataFrame(
            {"original_bank_name": bank_names, "swift": swift_codes}
        )


if input_df is not None and not input_df.empty:
    input_df["original_bank_name"] = (
        input_df["original_bank_name"].fillna("").astype(str).replace({"nan": "", "None": ""})
    )
    input_df["swift_raw"] = (
        input_df["swift"].fillna("").astype(str).replace({"nan": "", "None": ""})
    )
    parsed_swift = input_df["swift_raw"].map(normalise_swift)
    input_df["swift"] = parsed_swift.map(lambda item: item["original"])
    input_df["lookup_code"] = parsed_swift.map(lambda item: item["lookup_code"])
    input_df["institution_key"] = parsed_swift.map(lambda item: item["institution_key"])
    input_df["swift_format_ok"] = parsed_swift.map(lambda item: item["ok"])
    input_df["swift_format_error"] = parsed_swift.map(lambda item: item["error"])
    input_df["row_number"] = range(1, len(input_df) + 1)

    unique_keys = sorted(
        {
            key
            for key, ok in zip(input_df["institution_key"], input_df["swift_format_ok"])
            if ok and key
        }
    )
    cached_hits = sum(1 for key in unique_keys if key in st.session_state.lookup_success_cache)
    new_lookups = len(unique_keys) - cached_hits
    empty_names = int((input_df["original_bank_name"].str.strip() == "").sum())
    empty_swifts = int((input_df["swift"].str.strip() == "").sum())

    st.subheader("Input preview")
    st.caption(
        f"{len(input_df)} row(s) • {empty_names} empty bank name(s) • "
        f"{empty_swifts} empty SWIFT(s) • {len(unique_keys)} unique SWIFT(s) • "
        f"{cached_hits} cached • {new_lookups} new lookup(s)"
    )
    st.dataframe(
        input_df[["row_number", "original_bank_name", "swift_raw"]],
        use_container_width=True,
        hide_index=True,
    )


with st.expander("⚙️ Settings", expanded=False):
    delay = st.number_input(
        "Delay between new SWIFT lookups (seconds)",
        min_value=0.0,
        max_value=5.0,
        value=0.4,
        step=0.1,
    )


st.header("2. Correct bank names")
generate_output = st.button(
    "🚀 Generate Output",
    type="primary",
    use_container_width=True,
    disabled=input_df is None or input_df.empty,
)

if generate_output:
    progress = st.progress(0)
    status_text = st.empty()
    processing_started = datetime.now()
    results = []
    change_log = []
    rate_limit_hit = False
    lookups_this_run = 0
    unique_lookup_map: dict[str, dict] = {}

    unique_rows = (
        input_df[input_df["swift_format_ok"]]
        .drop_duplicates("institution_key")
        [["institution_key", "lookup_code"]]
    )

    for i, row in enumerate(unique_rows.itertuples(index=False), start=1):
        key = row.institution_key
        lookup_code = row.lookup_code
        status_text.write(f"Looking up {i} of {len(unique_rows)} — `{lookup_code}`")

        if key in st.session_state.lookup_success_cache:
            unique_lookup_map[key] = st.session_state.lookup_success_cache[key]
        elif rate_limit_hit:
            unique_lookup_map[key] = {
                "valid": False,
                "error": "Skipped because all API keys were rate limited earlier in this run",
                "retryable": True,
            }
        else:
            info = lookup_swift(lookup_code)
            lookups_this_run += 1
            if info.get("valid"):
                st.session_state.lookup_success_cache[key] = info
            elif info.get("retryable") and "rate limit" in info.get("error", "").lower():
                if not [k for k in API_KEYS if k not in st.session_state.exhausted_keys]:
                    rate_limit_hit = True
            unique_lookup_map[key] = info
            if delay > 0:
                time.sleep(delay)

        progress.progress(i / max(len(unique_rows), 1) * 0.7)

    for index, row in input_df.iterrows():
        original_name = row["original_bank_name"]
        swift = row["swift"]
        key = row["institution_key"]
        row_number = row["row_number"]

        if not row["swift_format_ok"]:
            results.append(
                {
                    "row_number": row_number,
                    "original_bank_name": original_name,
                    "official_bank_name": "",
                    "final_bank_name": original_name,
                    "swift": swift,
                    "official_swift": "",
                    "status": "INVALID SWIFT",
                    "change_made": "NO",
                    "city": "",
                    "country": "",
                    "note": row["swift_format_error"] or "SWIFT could not be validated",
                }
            )
            continue

        info = unique_lookup_map.get(key) or {"valid": False, "error": "No lookup result"}
        if not info.get("valid"):
            status = (
                "RATE LIMITED"
                if info.get("retryable") and "rate limit" in info.get("error", "").lower()
                else "INVALID SWIFT"
            )
            if "Skipped because" in info.get("error", ""):
                status = "RATE LIMITED"
            results.append(
                {
                    "row_number": row_number,
                    "original_bank_name": original_name,
                    "official_bank_name": "",
                    "final_bank_name": original_name,
                    "swift": swift,
                    "official_swift": "",
                    "status": status,
                    "change_made": "NO",
                    "city": "",
                    "country": "",
                    "note": info.get("error", "SWIFT could not be validated"),
                }
            )
            continue

        official_name = info["official_bank_name"]
        official_swift = info.get("official_swift", "")

        if not original_name.strip():
            final_name = official_name
            status = "FILLED"
            change_made = "YES"
            note = "Blank bank name replaced with the official SWIFT bank name."
        elif names_are_same(original_name, official_name):
            final_name = official_name
            status = "CORRECT"
            change_made = "NO"
            note = "Bank name already matched the official SWIFT bank name."
        else:
            final_name = official_name
            status = "CORRECTED"
            change_made = "YES"
            note = "Bank name replaced with the official SWIFT bank name."

        if change_made == "YES":
            change_log.append(
                {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "row_number": row_number,
                    "swift": swift,
                    "official_swift": official_swift,
                    "original_bank_name": original_name,
                    "official_bank_name": official_name,
                    "city": info.get("city", ""),
                    "country": info.get("country", ""),
                    "reason": note,
                }
            )

        results.append(
            {
                "row_number": row_number,
                "original_bank_name": original_name,
                "official_bank_name": official_name,
                "final_bank_name": final_name,
                "swift": swift,
                "official_swift": official_swift,
                "status": status,
                "change_made": change_made,
                "city": info.get("city", ""),
                "country": info.get("country", ""),
                "note": note,
            }
        )
        progress.progress(0.7 + 0.3 * ((index + 1) / len(input_df)))

    status_text.empty()
    progress.empty()
    output_df = pd.DataFrame(results)
    change_log_df = pd.DataFrame(change_log)
    st.session_state.results = {
        "output_df": output_df,
        "change_log_df": change_log_df,
        "lookups_this_run": lookups_this_run,
        "unique_lookups": len(unique_rows),
        "processing_seconds": (datetime.now() - processing_started).total_seconds(),
        "rate_limit_hit": rate_limit_hit,
    }


state = st.session_state.results
if state is None:
    st.stop()

output_df = state["output_df"]
change_log_df = state["change_log_df"]
counts = output_df["status"].value_counts()

st.header("3. Summary")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total records", len(output_df))
c2.metric("Already correct", int(counts.get("CORRECT", 0)))
c3.metric("Names corrected", int(counts.get("CORRECTED", 0)))
c4.metric("Blank names filled", int(counts.get("FILLED", 0)))
c5.metric(
    "Invalid / limited",
    int(counts.get("INVALID SWIFT", 0) + counts.get("RATE LIMITED", 0)),
)

if state["rate_limit_hit"]:
    st.error("All API keys hit the rate limit. Remaining codes were left unchanged.")

st.header("4. Corrected names")
corrected_df = output_df[output_df["status"].isin(["CORRECTED", "FILLED"])]
if corrected_df.empty:
    st.info("No bank names needed correction.")
else:
    st.dataframe(
        corrected_df[
            [
                "row_number",
                "original_bank_name",
                "official_bank_name",
                "final_bank_name",
                "swift",
                "status",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

st.header("5. Invalid or rate-limited SWIFT codes")
invalid_df = output_df[output_df["status"].isin(["INVALID SWIFT", "RATE LIMITED"])]
if invalid_df.empty:
    st.success("No invalid or rate-limited SWIFT codes.")
else:
    st.dataframe(
        invalid_df[["row_number", "original_bank_name", "swift", "status", "note"]],
        use_container_width=True,
        hide_index=True,
    )

st.header("6. Change log")
st.markdown("Every bank-name replacement is recorded here, including the original Excel row number.")
if change_log_df.empty:
    st.info("No changes were made.")
else:
    st.dataframe(change_log_df, use_container_width=True, hide_index=True)

st.header("7. Final cleaned data")
st.markdown(
    "`final_bank_name` is the official corrected value. "
    "Row order matches the pasted Excel data, including empty cells."
)
st.dataframe(output_df, use_container_width=True, hide_index=True)

st.header("8. Download")
cleaned_df = output_df[
    ["row_number", "final_bank_name", "official_bank_name", "swift", "official_swift", "status"]
]
d1, d2, d3 = st.columns(3)
d1.download_button(
    "⬇️ Download cleaned names",
    data=dataframe_to_csv_bytes(cleaned_df),
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
    "⬇️ Download full audit",
    data=dataframe_to_csv_bytes(output_df),
    file_name="bank_swift_audit.csv",
    mime="text/csv",
    use_container_width=True,
)

st.caption(
    f"Processed {len(output_df)} records with {state['unique_lookups']} unique SWIFT lookup(s), "
    f"{state['lookups_this_run']} new API call(s), "
    f"in {state['processing_seconds']:.2f} seconds."
)