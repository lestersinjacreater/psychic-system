import io
import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Bank Name & SWIFT Data Cleaner",
    page_icon="🏦",
    layout="wide",
)


# ============================================================
# PAGE HEADER
# ============================================================

st.title("🏦 Bank Name & SWIFT Data Cleaner")

st.markdown(
    """
    ### Clean bank names using their SWIFT codes

    Paste your **Bank Name column** and **SWIFT column** from Excel below.

    The application will:

    1. Validate each SWIFT code using the SWIFT lookup API.
    2. Retrieve the official bank name associated with the SWIFT code.
    3. Compare it with the bank name in your data.
    4. Keep the existing name when it already matches.
    5. Replace an incorrect name with the official bank name.
    6. Flag SWIFT codes that cannot be found or validated.
    7. Keep a complete log of every bank-name correction.
    """
)

st.info(
    "💡 The SWIFT code is used as the source of truth for determining "
    "the correct bank name."
)


# ============================================================
# API CONFIGURATION
# ============================================================

API_URL = "https://api.api-ninjas.com/v1/swiftcode"

try:
    API_KEY = st.secrets.get("API_NINJAS_KEY", "")
except Exception:
    API_KEY = ""


if not API_KEY:
    st.error(
        "API key not configured. Add `API_NINJAS_KEY` "
        "to your Streamlit secrets."
    )
    st.stop()


# ============================================================
# DATA INPUT SECTION
# ============================================================

st.header("1. Paste your data")

st.markdown(
    """
    Copy the two columns directly from Excel and paste them into the
    corresponding boxes below.

    **Important:** The bank name and SWIFT code are matched by their
    position. The first bank name is matched with the first SWIFT code,
    the second bank name with the second SWIFT code, and so on.
    """
)

col1, col2 = st.columns(2)


with col1:

    st.subheader("🏦 Bank Name Column")

    bank_names_raw = st.text_area(
        "Paste Bank Names",
        height=300,
        placeholder=(
            "JPMorgan Chase\n"
            "Deutsche Bank\n"
            "Barclays Bank Kenya\n"
            "Absa Bank Kenya\n"
            "..."
        ),
        label_visibility="collapsed",
    )

    st.caption(
        "Paste one bank name per line. A header such as "
        "`Bank Name` is allowed."
    )


with col2:

    st.subheader("🔑 SWIFT Code Column")

    swift_codes_raw = st.text_area(
        "Paste SWIFT Codes",
        height=300,
        placeholder=(
            "CHASUS33\n"
            "DEUTDEFF\n"
            "BARCKENX\n"
            "ABSAKENX\n"
            "..."
        ),
        label_visibility="collapsed",
    )

    st.caption(
        "Paste one SWIFT code per line. A header such as "
        "`SWIFT` or `BIC` is allowed."
    )


# ============================================================
# ADVANCED SETTINGS
# ============================================================

with st.expander("⚙️ Advanced settings"):

    delay = st.number_input(
        "Delay between new SWIFT lookups (seconds)",
        min_value=0.0,
        max_value=5.0,
        value=1.0,
        step=0.5,
        help=(
            "Adds a delay between API requests to reduce the chance "
            "of hitting API rate limits."
        ),
    )


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def split_lines(text: str) -> list[str]:
    """
    Convert pasted column data into a clean list of values.
    """

    if not text:
        return []

    lines = text.splitlines()

    values = []

    for line in lines:

        value = line.strip()

        if value:
            values.append(value)

    return values


def remove_optional_header(values: list[str], column_type: str) -> list[str]:
    """
    Remove an optional header from a pasted column.
    """

    if not values:
        return values

    first = values[0].strip().lower()

    if column_type == "bank":

        possible_headers = {
            "bank",
            "bank name",
            "bank_name",
            "name",
        }

    else:

        possible_headers = {
            "swift",
            "swift code",
            "swift_code",
            "bic",
            "bic code",
            "code",
        }

    if first in possible_headers:

        return values[1:]

    return values


def clean_swift(swift: str) -> str:
    """
    Normalise a SWIFT/BIC value.
    """

    return (
        str(swift)
        .strip()
        .upper()
        .replace(" ", "")
        .replace("\t", "")
    )


def normalise_bank_name(name: str) -> str:
    """
    Normalise bank names for comparison.

    This is NOT used to generate the final bank name.
    It is only used to determine whether the supplied name
    is effectively the same as the official name.
    """

    if not name:
        return ""

    value = str(name).upper().strip()

    # Replace common punctuation with spaces
    punctuation = [
        ".",
        ",",
        "-",
        "/",
        "\\",
        "&",
        "(",
        ")",
    ]

    for character in punctuation:
        value = value.replace(character, " ")

    # Common legal/business suffixes.
    # These should not cause an otherwise identical bank name
    # to be considered different.
    removable_words = {
        "BANK",
        "N A",
        "NA",
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
    }

    words = value.split()

    words = [
        word
        for word in words
        if word not in removable_words
    ]

    return " ".join(words)


def names_are_same(
    supplied_name: str,
    official_name: str,
) -> bool:
    """
    Determine whether the supplied and official bank names
    are effectively the same after normalisation.
    """

    supplied = normalise_bank_name(supplied_name)
    official = normalise_bank_name(official_name)

    return supplied == official


# ============================================================
# SWIFT API LOOKUP
# ============================================================

@st.cache_data(show_spinner=False)
def lookup_swift(code: str) -> dict:
    """
    Look up a SWIFT code using API Ninjas.
    """

    if not code:

        return {
            "valid": False,
            "error": "Empty SWIFT code",
        }

    try:

        response = requests.get(
            API_URL,
            headers={
                "X-Api-Key": API_KEY,
            },
            params={
                "swift": code,
            },
            timeout=20,
        )

    except requests.RequestException as error:

        return {
            "valid": False,
            "error": f"API request failed: {error}",
        }

    # --------------------------------------------------------
    # RATE LIMIT
    # --------------------------------------------------------

    if response.status_code == 429:

        return {
            "valid": False,
            "error": "API rate limit reached",
        }

    # --------------------------------------------------------
    # OTHER HTTP ERRORS
    # --------------------------------------------------------

    if response.status_code != 200:

        return {
            "valid": False,
            "error": f"API returned HTTP {response.status_code}",
        }

    # --------------------------------------------------------
    # PARSE RESPONSE
    # --------------------------------------------------------

    try:

        data = response.json()

    except ValueError:

        return {
            "valid": False,
            "error": "API returned an invalid response",
        }

    # --------------------------------------------------------
    # NO RECORD
    # --------------------------------------------------------

    if not data:

        return {
            "valid": False,
            "error": "SWIFT code not found",
        }

    # API normally returns a list
    hit = data[0] if isinstance(data, list) else data

    official_name = (
        hit.get("bank_name")
        or hit.get("name")
        or ""
    )

    if not official_name:

        return {
            "valid": False,
            "error": "SWIFT found but no bank name was returned",
        }

    return {
        "valid": True,
        "official_bank_name": official_name,
        "official_swift": (
            hit.get("swift_code")
            or hit.get("swift")
            or code
        ),
        "city": hit.get("city") or "",
        "country": hit.get("country") or "",
    }


# ============================================================
# GENERATE OUTPUT BUTTON
# ============================================================

st.header("2. Generate cleaned data")

generate_output = st.button(
    "🚀 Generate Output",
    type="primary",
    use_container_width=True,
)


# ============================================================
# VALIDATION & PROCESSING
# ============================================================

if generate_output:

    # --------------------------------------------------------
    # READ INPUT
    # --------------------------------------------------------

    bank_names = split_lines(bank_names_raw)
    swift_codes = split_lines(swift_codes_raw)

    bank_names = remove_optional_header(
        bank_names,
        "bank",
    )

    swift_codes = remove_optional_header(
        swift_codes,
        "swift",
    )

    # --------------------------------------------------------
    # CHECK FOR EMPTY INPUT
    # --------------------------------------------------------

    if not bank_names:

        st.error(
            "No bank names were provided. "
            "Paste the Bank Name column from Excel."
        )

        st.stop()

    if not swift_codes:

        st.error(
            "No SWIFT codes were provided. "
            "Paste the SWIFT column from Excel."
        )

        st.stop()

    # --------------------------------------------------------
    # CHECK ROW COUNTS
    # --------------------------------------------------------

    if len(bank_names) != len(swift_codes):

        st.error(
            f"❌ The number of bank names and SWIFT codes does not match.\n\n"
            f"Bank names: **{len(bank_names)}**\n\n"
            f"SWIFT codes: **{len(swift_codes)}**\n\n"
            "Please check your Excel data before continuing."
        )

        st.stop()

    # --------------------------------------------------------
    # BUILD INPUT DATAFRAME
    # --------------------------------------------------------

    input_df = pd.DataFrame(
        {
            "original_bank_name": bank_names,
            "swift": [
                clean_swift(code)
                for code in swift_codes
            ],
        }
    )

    # --------------------------------------------------------
    # CHECK FOR EMPTY SWIFT VALUES
    # --------------------------------------------------------

    empty_swift_rows = input_df[
        input_df["swift"] == ""
    ]

    if not empty_swift_rows.empty:

        st.error(
            f"{len(empty_swift_rows)} row(s) contain an empty "
            "SWIFT code. Please correct the input before continuing."
        )

        st.stop()

    # ========================================================
    # PROCESSING
    # ========================================================

    st.subheader("Processing")

    progress = st.progress(0)

    status_text = st.empty()

    results = []

    change_log = []

    swift_cache = {}

    processing_started = datetime.now()

    # --------------------------------------------------------
    # PROCESS EACH ROW
    # --------------------------------------------------------

    for index, row in input_df.iterrows():

        original_name = row["original_bank_name"]

        swift = row["swift"]

        status_text.write(
            f"Processing row {index + 1} of "
            f"{len(input_df)} — SWIFT: `{swift}`"
        )

        # ----------------------------------------------------
        # LOOKUP SWIFT ONLY ONCE
        # ----------------------------------------------------

        if swift not in swift_cache:

            swift_cache[swift] = lookup_swift(swift)

            if delay > 0:
                time.sleep(delay)

        info = swift_cache[swift]

        # ----------------------------------------------------
        # INVALID SWIFT
        # ----------------------------------------------------

        if not info.get("valid"):

            results.append(
                {
                    "original_bank_name": original_name,

                    "swift": swift,

                    "official_bank_name": "",

                    "final_bank_name": original_name,

                    "status": "INVALID SWIFT",

                    "change_made": "NO",

                    "city": "",

                    "country": "",

                    "note": info.get(
                        "error",
                        "SWIFT could not be validated",
                    ),
                }
            )

        # ----------------------------------------------------
        # VALID SWIFT
        # ----------------------------------------------------

        else:

            official_name = info["official_bank_name"]

            same_name = names_are_same(
                original_name,
                official_name,
            )

            # ------------------------------------------------
            # NAME IS ALREADY CORRECT
            # ------------------------------------------------

            if same_name:

                final_name = original_name

                status = "CORRECT"

                change_made = "NO"

                note = (
                    "Bank name matches the official "
                    "SWIFT bank name."
                )

            # ------------------------------------------------
            # NAME NEEDS CORRECTION
            # ------------------------------------------------

            else:

                final_name = official_name

                status = "CORRECTED"

                change_made = "YES"

                note = (
                    "Bank name replaced with the official "
                    "bank name returned for the SWIFT code."
                )

                # --------------------------------------------
                # AUDIT LOG
                # --------------------------------------------

                change_log.append(
                    {
                        "timestamp": datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),

                        "swift": swift,

                        "original_bank_name": original_name,

                        "corrected_bank_name": official_name,

                        "city": info["city"],

                        "country": info["country"],

                        "reason": (
                            "Original bank name differed from "
                            "the official SWIFT bank name."
                        ),
                    }
                )

            # ------------------------------------------------
            # ADD RESULT
            # ------------------------------------------------

            results.append(
                {
                    "original_bank_name": original_name,

                    "swift": swift,

                    "official_bank_name": official_name,

                    "final_bank_name": final_name,

                    "status": status,

                    "change_made": change_made,

                    "city": info["city"],

                    "country": info["country"],

                    "note": note,
                }
            )

        # ----------------------------------------------------
        # UPDATE PROGRESS
        # ----------------------------------------------------

        progress.progress(
            (index + 1) / len(input_df)
        )

    processing_finished = datetime.now()

    status_text.empty()

    # ========================================================
    # OUTPUT DATAFRAMES
    # ========================================================

    output_df = pd.DataFrame(results)

    change_log_df = pd.DataFrame(change_log)

    # ========================================================
    # SUMMARY
    # ========================================================

    total_rows = len(output_df)

    correct_rows = (
        output_df["status"] == "CORRECT"
    ).sum()

    corrected_rows = (
        output_df["status"] == "CORRECTED"
    ).sum()

    invalid_rows = (
        output_df["status"] == "INVALID SWIFT"
    ).sum()

    # ========================================================
    # SUMMARY DISPLAY
    # ========================================================

    st.header("3. Cleaning Summary")

    summary_col1, summary_col2, summary_col3, summary_col4 = (
        st.columns(4)
    )

    summary_col1.metric(
        "Total Records",
        total_rows,
    )

    summary_col2.metric(
        "Correct Names",
        correct_rows,
    )

    summary_col3.metric(
        "Names Corrected",
        corrected_rows,
    )

    summary_col4.metric(
        "Invalid SWIFT",
        invalid_rows,
    )

    # ========================================================
    # STATUS MESSAGE
    # ========================================================

    if invalid_rows > 0:

        st.warning(
            f"⚠️ {invalid_rows} record(s) contain SWIFT codes "
            "that could not be validated. These records were "
            "flagged and their original bank names were preserved."
        )

    if corrected_rows > 0:

        st.success(
            f"✅ {corrected_rows} bank name(s) were corrected "
            "using the official SWIFT information."
        )

    if corrected_rows == 0 and invalid_rows == 0:

        st.success(
            "✅ All bank names matched their SWIFT records. "
            "No corrections were necessary."
        )

    # ========================================================
    # CORRECTED RECORDS
    # ========================================================

    st.header("4. Corrected Bank Names")

    corrected_df = output_df[
        output_df["status"] == "CORRECTED"
    ]

    if corrected_df.empty:

        st.info(
            "No bank names needed correction."
        )

    else:

        st.dataframe(
            corrected_df,
            use_container_width=True,
            hide_index=True,
        )

    # ========================================================
    # INVALID SWIFT CODES
    # ========================================================

    st.header("5. Invalid SWIFT Codes")

    invalid_df = output_df[
        output_df["status"] == "INVALID SWIFT"
    ]

    if invalid_df.empty:

        st.success(
            "No invalid or unrecognised SWIFT codes were found."
        )

    else:

        st.dataframe(
            invalid_df[
                [
                    "original_bank_name",
                    "swift",
                    "final_bank_name",
                    "status",
                    "note",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    # ========================================================
    # CHANGE LOG
    # ========================================================

    st.header("6. Change Log")

    st.markdown(
        """
        This log records every bank-name change made by the system.
        The original name is preserved so that every correction can
        be traced back to the source data.
        """
    )

    if change_log_df.empty:

        st.info(
            "No changes were made."
        )

    else:

        st.dataframe(
            change_log_df,
            use_container_width=True,
            hide_index=True,
        )

    # ========================================================
    # FULL OUTPUT
    # ========================================================

    st.header("7. Final Cleaned Data")

    st.markdown(
        """
        This is the final dataset.

        **`final_bank_name`** is the column you should use as the
        cleaned bank-name value in your downstream process.
        """
    )

    st.dataframe(
        output_df,
        use_container_width=True,
        hide_index=True,
    )

    # ========================================================
    # DOWNLOAD SECTION
    # ========================================================

    st.header("8. Download")

    download_col1, download_col2, download_col3 = st.columns(3)

    # --------------------------------------------------------
    # CLEANED DATA
    # --------------------------------------------------------

    cleaned_csv = output_df.to_csv(
        index=False
    ).encode("utf-8")

    download_col1.download_button(
        label="⬇️ Download Cleaned Data",
        data=cleaned_csv,
        file_name="cleaned_bank_data.csv",
        mime="text/csv",
        use_container_width=True,
    )

    # --------------------------------------------------------
    # CHANGE LOG
    # --------------------------------------------------------

    change_csv = change_log_df.to_csv(
        index=False
    ).encode("utf-8")

    download_col2.download_button(
        label="⬇️ Download Change Log",
        data=change_csv,
        file_name="bank_name_change_log.csv",
        mime="text/csv",
        use_container_width=True,
    )

    # --------------------------------------------------------
    # FULL AUDIT
    # --------------------------------------------------------

    audit_csv = output_df.to_csv(
        index=False
    ).encode("utf-8")

    download_col3.download_button(
        label="⬇️ Download Full Audit",
        data=audit_csv,
        file_name="bank_swift_audit.csv",
        mime="text/csv",
        use_container_width=True,
    )

    # ========================================================
    # PROCESSING INFORMATION
    # ========================================================

    processing_time = (
        processing_finished - processing_started
    ).total_seconds()

    st.caption(
        f"Processed {total_rows} records using "
        f"{len(swift_cache)} unique SWIFT lookup(s) "
        f"in {processing_time:.2f} seconds."
    )