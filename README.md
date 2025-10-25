# Apache Management MCP Server (SSE Transport)

A small MCP-compatible server that exposes Apache site management functions (list, enable/disable, test, reload, restart) over SSE + HTTP. Includes API Key authentication for controlling access.

This repository hosts `apache-mcp-sse.py` — a Starlette/uvicorn-based ASGI app that wraps common Apache admin commands and presents them as MCP resources and tools.

## Features
- List sites in `/etc/apache2/sites-available` and `/etc/apache2/sites-enabled`
- Read site configuration files
- Enable / disable sites via `a2ensite` / `a2dissite`
- Test Apache configuration and reload/restart the service
- SSE transport for MCP client connections
- API Key authentication (header: `X-API-Key`)
- CORS enabled (default: allow all origins)

## Prerequisites
- Linux (Debian/Ubuntu-style Apache layout)
- Python 3.12+ (project requires `>=3.12`)
- sudo access for running `a2ensite`, `a2dissite`, `apache2ctl`, and `service apache2` commands
- uv package manager (no pip usage)

Install uv (one-time):
```bash
# Linux/macOS (from Astral)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify
uv --version
```

## Project layout
- `apache-mcp-sse.py` — main SSE + HTTP server for MCP
- `apache-rest-api.py` — optional REST/FastAPI variant (if you prefer REST over SSE)
- `pyproject.toml` — project metadata and dependencies (`fastapi`, `httpx`, `mcp[cli]`, etc.)

## Setup with uv (no pip)
Sync the project environment from `pyproject.toml`:
```bash
# Create/refresh a project-local virtualenv and install dependencies
uv sync
```

If you plan to use the SSE server (`apache-mcp-sse.py`) and it's missing runtime deps in your `pyproject.toml`, add them with uv (once):
```bash
# Only if needed
uv add starlette uvicorn
```

Note: The project already lists `mcp[cli]`, `fastapi`, and `httpx`. The SSE server uses `starlette` and `uvicorn` at runtime.

## Running the server (uv)
Set an API key (recommended) or let the server auto-generate one on startup.

```bash
# 1) Set a permanent API key (recommended)
export MCP_API_KEY="my-super-secret-key-12345"

# 2) Run the SSE server under uv's environment
uv run python apache-mcp-sse.py
```

On startup the server prints connection info including the active API key (if generated) and endpoints.

Default bind: `http://0.0.0.0:8000`

### Endpoints
- `GET /` — server info (no auth)
- `GET /health` — health check (no auth)
- `GET /sse` — SSE transport (requires `X-API-Key`)
- `POST /messages` — MCP message ingress (requires `X-API-Key`)

## 🔐 API key authentication usage

Set `MCP_API_KEY` as shown above under "Running the server" (or the server will auto-generate one on startup).

### Test with curl
```bash
# WITHOUT API key (will fail)
curl http://localhost:8000/sse
# Response: {"error":"Authentication required","message":"Missing X-API-Key header"}

# WITH API key (will work)
curl -H "X-API-Key: my-super-secret-key-12345" \
     http://localhost:8000/sse

# Health check (no auth needed)
curl http://localhost:8000/health
```

### Apache reverse proxy (optional)
To have Apache forward requests and inject the API key header so clients don't need to include it:

```apache
<VirtualHost *:80>
    ServerName <domain>

    ProxyPreserveHost On
    ProxyTimeout 3600

    # Pass the API key header
    RequestHeader set X-API-Key "my-super-secret-key-12345"

    SetEnv proxy-nokeepalive 1
    SetEnv proxy-initial-not-pooled 1

    <Location /sse>
        ProxyPass http://127.0.0.1:8000/sse
        ProxyPassReverse http://127.0.0.1:8000/sse
        SetEnv proxy-sendcl 0
        SetEnv proxy-sendchunked 1
    </Location>

    <Location /messages>
        ProxyPass http://127.0.0.1:8000/messages
        ProxyPassReverse http://127.0.0.1:8000/messages
    </Location>

    <Location /health>
        ProxyPass http://127.0.0.1:8000/health
        ProxyPassReverse http://127.0.0.1:8000/health
    </Location>
</VirtualHost>
```

Then the client config can omit headers:
```json
{
  "mcpServers": {
    "apache-manager": {
      "url": "http://test.kraybin.com/sse"
    }
  }
}
```

### Generate a strong API key
```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Security features
- API key required for `/sse` and `/messages`
- No authentication needed for `/health` and `/` (info only)
- Auto-generated key if not set
- Environment variable support (`MCP_API_KEY`)
- Custom header name (`X-API-Key`)

## Using with MCP Inspector
The server prints a one-liner for the MCP Inspector. You can also run:
```bash
# Replace host if remote
npx @modelcontextprotocol/inspector http://localhost:8000/sse
```
Add the `X-API-Key` header in the Inspector UI if not using a reverse proxy that injects it.

## Available MCP tools
- `list_available_sites` — list all files under `/etc/apache2/sites-available`
- `list_enabled_sites` — list all files under `/etc/apache2/sites-enabled`
- `get_site_status` — show enabled status and configuration for a site
- `enable_site` — enable a site via `a2ensite` (optional auto-reload)
- `disable_site` — disable a site via `a2dissite` (optional auto-reload)
- `test_config` — run `apache2ctl configtest`
- `reload_apache` — `service apache2 reload`
- `restart_apache` — `service apache2 restart`

## Troubleshooting
- Permission issues with `a2ensite`/`a2dissite`: configure sudoers for the service user or run under a user with appropriate privileges. Non-interactive sudo may require NOPASSWD rules.
- SSE via reverse proxy: ensure long-lived connections are allowed and chunked encoding is enabled (see example above).
- Empty lists: verify the standard Debian/Ubuntu Apache layout exists and files are readable (`/etc/apache2/sites-available`, `/etc/apache2/sites-enabled`).

## Security notes
- Protect your API key; it grants control over Apache site configuration and reloads.
- In production, restrict CORS/allowed origins and use HTTPS behind a reverse proxy.
- Keep the generated key out of shared logs; it prints to stdout on startup if not provided.