# psychic-system
validation of banks

## Required setup

This app uses the API Ninjas SWIFT lookup endpoint and expects your key in Streamlit secrets.

Create a file at `.streamlit/secrets.toml` with:

```toml
API_NINJAS_KEY = "YOUR_API_KEY_HERE"
```

Then run:

```bash
streamlit run app.py
```
