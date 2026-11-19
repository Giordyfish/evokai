# `microsoft_files`: SharePoint and OneDrive

Search and read files inside allowed folders of a SharePoint document library or a OneDrive, through Microsoft
Graph with an app registration (client credentials). Read-only.

```yaml
mcp_servers:
  - name: policies
    connector: microsoft_files
    config:
      tenant_id: 00000000-0000-0000-0000-000000000000
      client_id: 11111111-1111-1111-1111-111111111111
      client_secret_env: GRAPH_CLIENT_SECRET
      site_id: contoso.sharepoint.com,<site guid>,<web guid>   # the site's default library
      # or drive_id: b!xxxx                                     # a specific library or a OneDrive
      folders: [/Policies, /HR/Handbook]                        # paths in that drive
      max_file_chars: 20000
```

Tools: `search_files` (within each folder), `list_folder` (a folder or a folder inside one), `read_file`.

- A file is read only when its folder is one of `folders` or below one; anything else is refused.
- Word, Excel and PowerPoint files are read as text (no conversion service), as are text, CSV, JSON and HTML files.

Permissions: give the app registration Microsoft Graph **application** permission `Sites.Selected` (and grant it
read on the site) or `Files.Read.All`, with admin consent. Store its client secret in your secret store.
