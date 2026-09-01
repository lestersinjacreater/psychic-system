# psychic-system
validation of banks

## Required setup

This app can use the local JSON BIC directory and will fall back to the IsValid API when a code is not found locally.

Create a file at `.streamlit/secrets.toml` with:

```toml
IS_VALID_API_KEY = "YOUR_API_KEY_HERE"
```

The app calls the endpoint in the format:

```python
requests.get(
    'https://api.isvalid.dev/v0/bic?value=XASXAU2SRTG',
    headers={'Authorization': 'Bearer YOUR_API_KEY'}
)
```

Then run:

```bash
streamlit run app.py
```
