# `sql`: SQL databases

Postgres, MySQL/MariaDB, SQLite, SQL Server: any database SQLAlchemy reaches asynchronously. The agent never writes
SQL: it asks for rows of an allowed table with structured filters, or for grouped figures, and the connector builds
the statement from the table's reflected columns, with every value a bound parameter. Every read runs in a read-only
transaction (Postgres: `SET TRANSACTION READ ONLY` and a statement timeout; SQLite: `query_only`) that is rolled back.

```yaml
mcp_servers:
  - name: crm
    connector: sql
    config:
      dsn_env: CRM_DATABASE_URL        # e.g. postgresql+asyncpg://reader@db:5432/crm (postgres://... works too)
      tables: [public.customers, public.invoices, reporting.*]
      hidden_columns: {public.customers: [email, phone, tax_code]}
      row_limit: 100                    # 1-1000
      timeout_seconds: 10
      queries:                          # optional: SQL you wrote, offered as tools with typed parameters
        overdue_by_city:
          description: Overdue invoices of the customers of a city, oldest first.
          sql: >
            SELECT c.name, i.number, i.due_date, i.amount FROM public.invoices i
            JOIN public.customers c ON c.id = i.customer_id
            WHERE i.status = 'overdue' AND c.city = :city ORDER BY i.due_date
          params: {city: string}        # string, integer, number, boolean, date
      writes: false                     # true adds insert_row and update_rows for write_tables
      write_tables: []
```

| Key | Meaning |
| --- | --- |
| `dsn_env` | Environment variable with the database URL. Use a database user with read rights only (and write rights on `write_tables` when `writes` is on). |
| `tables` | Allowed tables: `name`, `schema.name` or `schema.*`. Anything else is invisible. |
| `hidden_columns` | Per table, columns never read, filtered on or shown. |
| `row_limit` | Most rows any call returns (`more: true` says there were more). |
| `queries` | Named queries: one `SELECT`/`WITH` statement each, no `;`, no data-changing keywords, `:name` parameters. |
| `writes`, `write_tables` | Opt-in writes, only to these tables, each one confirmed by the user. Updates need filters. |

Tools: `list_tables`, `describe_table`, `query` (columns, filters with `eq ne lt le gt ge like in is_null not_null`,
`order_by`, `limit`), `aggregate` (`group_by`, metrics `count sum avg min max`), `query_<name>` per named query, and
with writes `insert_row`, `update_rows`.

TLS: put the driver's own option in the URL (asyncpg: `?ssl=require`).
