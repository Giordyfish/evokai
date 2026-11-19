# `google_drive`: Google Drive

Search and read files inside the folders you share with the assistant. Read-only (`drive.readonly` scope).

```yaml
mcp_servers:
  - name: drive
    connector: google_drive
    config:
      folders: [1AbCdEfGhIjKlMnOpQrStUvWxYz]   # folder ids (from the folder's URL)
      recursive: true                           # their sub-folders too (default)
      # An OAuth client and a refresh token of the account that owns or shares the folders:
      client_id_env: GOOGLE_CLIENT_ID
      client_secret_env: GOOGLE_CLIENT_SECRET
      refresh_token_env: GOOGLE_REFRESH_TOKEN
      # or a service account (its JSON key in one variable), with the folders shared to its e-mail:
      # service_account_env: GOOGLE_SERVICE_ACCOUNT_JSON
      max_file_chars: 20000
```

Tools: `search_files` (words in names and text, within the folders), `list_folder`, `read_file`.

- Every file is checked before it is read: its parents are walked up to an allowed folder. A file elsewhere is
  refused even when the credential could read it.
- Google Docs and Slides are exported as text, Sheets as CSV; uploaded Word, Excel and PowerPoint files, HTML and
  text files are read as text. Other types (PDF, images) are reported as not readable.

Getting the credentials: create an OAuth client (Desktop or Web) in Google Cloud with the Drive API enabled, then a
refresh token for the `https://www.googleapis.com/auth/drive.readonly` scope (for example with Google's OAuth
playground), and store the three values in your secret store. A service account avoids the refresh token: share
the folders with its e-mail address.
