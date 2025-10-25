#!/usr/bin/env python3
import asyncio
import json
import subprocess
import os
import secrets
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

from mcp.server import Server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
)
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import Response, JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import uvicorn

# Apache configuration paths
SITES_AVAILABLE = "/etc/apache2/sites-available"
SITES_ENABLED = "/etc/apache2/sites-enabled"

# API Key Authentication
API_KEY = os.getenv("MCP_API_KEY", "")
API_KEY_HEADER = "X-API-Key"

# Generate a secure API key if not set
if not API_KEY:
    API_KEY = secrets.token_urlsafe(32)
    print(f"\nNo MCP_API_KEY environment variable set!")
    print(f"Generated API Key: {API_KEY}")
    print(f"Set it permanently: export MCP_API_KEY='{API_KEY}'\n")

# Create MCP server
mcp_server = Server("apache-manager")

# Store SSE transport instance
sse_transport = None


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate API key for all requests except health check."""
    
    async def dispatch(self, request, call_next):
        # Skip authentication for health check and root info
        if request.url.path in ["/health", "/"]:
            return await call_next(request)
        
        # Check for API key in header
        provided_key = request.headers.get(API_KEY_HEADER)
        
        if not provided_key:
            return JSONResponse(
                {
                    "error": "Authentication required",
                    "message": f"Missing {API_KEY_HEADER} header"
                },
                status_code=401
            )
        
        if provided_key != API_KEY:
            return JSONResponse(
                {
                    "error": "Authentication failed",
                    "message": "Invalid API key"
                },
                status_code=403
            )
        
        # API key is valid, proceed with request
        response = await call_next(request)
        return response


def run_command(cmd: list[str]) -> tuple[bool, str, str]:
    """Execute a shell command and return success status, stdout, and stderr."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)


def list_sites(directory: str) -> list[str]:
    """List all site configuration files in a directory."""
    try:
        path = Path(directory)
        if not path.exists():
            return []
        
        sites = []
        for item in path.iterdir():
            if item.is_file() or item.is_symlink():
                if item.name not in ['.', '..', 'README']:
                    sites.append(item.name)
        
        return sorted(sites)
    except Exception as e:
        return []


def get_site_config(site_name: str) -> str:
    """Read the content of a site configuration file."""
    try:
        config_path = Path(SITES_AVAILABLE) / site_name
        if config_path.exists() and config_path.is_file():
            return config_path.read_text()
        return ""
    except Exception as e:
        return f"Error reading configuration: {str(e)}"


def is_site_enabled(site_name: str) -> bool:
    """Check if a site is currently enabled."""
    enabled_path = Path(SITES_ENABLED) / site_name
    return enabled_path.exists()


@mcp_server.list_resources()
async def handle_list_resources() -> list[Resource]:
    """List available Apache site resources."""
    resources = []
    
    available_sites = list_sites(SITES_AVAILABLE)
    for site in available_sites:
        enabled = is_site_enabled(site)
        status = "enabled" if enabled else "disabled"
        
        resources.append(
            Resource(
                uri=f"apache://sites-available/{site}",
                name=f"{site} ({status})",
                description=f"Apache site configuration - {status}",
                mimeType="text/plain"
            )
        )
    
    return resources


@mcp_server.read_resource()
async def handle_read_resource(uri: str) -> str:
    """Read the content of an Apache site configuration."""
    if not uri.startswith("apache://sites-available/"):
        raise ValueError(f"Unknown resource URI: {uri}")
    
    site_name = uri.replace("apache://sites-available/", "")
    config_content = get_site_config(site_name)
    
    if not config_content:
        raise ValueError(f"Site configuration not found: {site_name}")
    
    enabled = is_site_enabled(site_name)
    status = "ENABLED" if enabled else "DISABLED"
    
    return f"# Apache Site: {site_name}\n# Status: {status}\n\n{config_content}"


@mcp_server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """List available Apache management tools."""
    return [
        Tool(
            name="list_available_sites",
            description="List all available Apache site configurations in /etc/apache2/sites-available",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="list_enabled_sites",
            description="List all enabled Apache site configurations in /etc/apache2/sites-enabled",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_site_status",
            description="Get detailed status of a specific Apache site configuration",
            inputSchema={
                "type": "object",
                "properties": {
                    "site_name": {
                        "type": "string",
                        "description": "Name of the site configuration file (e.g., '000-default.conf')"
                    }
                },
                "required": ["site_name"]
            }
        ),
        Tool(
            name="enable_site",
            description="Enable an Apache site configuration using a2ensite",
            inputSchema={
                "type": "object",
                "properties": {
                    "site_name": {
                        "type": "string",
                        "description": "Name of the site to enable (e.g., '000-default.conf' or '000-default')"
                    },
                    "reload": {
                        "type": "boolean",
                        "description": "Whether to reload Apache after enabling the site",
                        "default": True
                    }
                },
                "required": ["site_name"]
            }
        ),
        Tool(
            name="disable_site",
            description="Disable an Apache site configuration using a2dissite",
            inputSchema={
                "type": "object",
                "properties": {
                    "site_name": {
                        "type": "string",
                        "description": "Name of the site to disable (e.g., '000-default.conf' or '000-default')"
                    },
                    "reload": {
                        "type": "boolean",
                        "description": "Whether to reload Apache after disabling the site",
                        "default": True
                    }
                },
                "required": ["site_name"]
            }
        ),
        Tool(
            name="test_config",
            description="Test Apache configuration for syntax errors using 'apache2ctl configtest'",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="reload_apache",
            description="Reload Apache configuration without dropping connections",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="restart_apache",
            description="Restart Apache web server (drops all connections)",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]


@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool execution requests."""
    
    if name == "list_available_sites":
        sites = list_sites(SITES_AVAILABLE)
        if not sites:
            return [TextContent(
                type="text",
                text="No available sites found in /etc/apache2/sites-available"
            )]
        
        result = "Available Apache Sites:\n\n"
        for site in sites:
            enabled = is_site_enabled(site)
            status = "✓ ENABLED" if enabled else "✗ disabled"
            result += f"  {status} - {site}\n"
        
        return [TextContent(type="text", text=result)]
    
    elif name == "list_enabled_sites":
        sites = list_sites(SITES_ENABLED)
        if not sites:
            return [TextContent(
                type="text",
                text="No enabled sites found in /etc/apache2/sites-enabled"
            )]
        
        result = "Enabled Apache Sites:\n\n"
        for site in sites:
            result += f"  ✓ {site}\n"
        
        return [TextContent(type="text", text=result)]
    
    elif name == "get_site_status":
        site_name = arguments["site_name"]
        
        available = list_sites(SITES_AVAILABLE)
        if site_name not in available:
            return [TextContent(
                type="text",
                text=f"Error: Site '{site_name}' not found in sites-available"
            )]
        
        enabled = is_site_enabled(site_name)
        config = get_site_config(site_name)
        
        result = f"Site: {site_name}\n"
        result += f"Status: {'ENABLED' if enabled else 'DISABLED'}\n"
        result += f"Available: Yes\n"
        result += f"Config Path: {SITES_AVAILABLE}/{site_name}\n"
        if enabled:
            result += f"Enabled Path: {SITES_ENABLED}/{site_name}\n"
        result += f"\nConfiguration:\n{'='*60}\n{config}\n"
        
        return [TextContent(type="text", text=result)]
    
    elif name == "enable_site":
        site_name = arguments["site_name"]
        reload = arguments.get("reload", True)
        
        available = list_sites(SITES_AVAILABLE)
        site_base = site_name.replace('.conf', '')
        
        site_exists = False
        for site in available:
            if site == site_name or site.replace('.conf', '') == site_base:
                site_exists = True
                break
        
        if not site_exists:
            return [TextContent(
                type="text",
                text=f"Error: Site '{site_name}' not found in sites-available"
            )]
        
        if is_site_enabled(site_name):
            return [TextContent(
                type="text",
                text=f"Site '{site_name}' is already enabled"
            )]
        
        success, stdout, stderr = run_command(["sudo", "a2ensite", site_name])
        
        if not success:
            return [TextContent(
                type="text",
                text=f"Error enabling site:\n{stderr}"
            )]
        
        result = f"Successfully enabled site: {site_name}\n{stdout}\n"
        
        if reload:
            reload_success, reload_out, reload_err = run_command(
                ["sudo", "service", "apache2", "reload"]
            )
            if reload_success:
                result += "\nApache configuration reloaded successfully"
            else:
                result += f"\nWarning: Failed to reload Apache:\n{reload_err}"
        else:
            result += "\nNote: Apache not reloaded. Run 'reload_apache' to apply changes."
        
        return [TextContent(type="text", text=result)]
    
    elif name == "disable_site":
        site_name = arguments["site_name"]
        reload = arguments.get("reload", True)
        
        if not is_site_enabled(site_name):
            return [TextContent(
                type="text",
                text=f"Site '{site_name}' is not enabled"
            )]
        
        success, stdout, stderr = run_command(["sudo", "a2dissite", site_name])
        
        if not success:
            return [TextContent(
                type="text",
                text=f"Error disabling site:\n{stderr}"
            )]
        
        result = f"Successfully disabled site: {site_name}\n{stdout}\n"
        
        if reload:
            reload_success, reload_out, reload_err = run_command(
                ["sudo", "service", "apache2", "reload"]
            )
            if reload_success:
                result += "\nApache configuration reloaded successfully"
            else:
                result += f"\nWarning: Failed to reload Apache:\n{reload_err}"
        else:
            result += "\nNote: Apache not reloaded. Run 'reload_apache' to apply changes."
        
        return [TextContent(type="text", text=result)]
    
    elif name == "test_config":
        success, stdout, stderr = run_command(["sudo", "apache2ctl", "configtest"])
        
        result = "Apache Configuration Test:\n\n"
        if success:
            result += "✓ Syntax OK\n"
        else:
            result += "✗ Configuration Error\n"
        
        if stdout:
            result += f"\nOutput:\n{stdout}"
        if stderr:
            result += f"\nErrors:\n{stderr}"
        
        return [TextContent(type="text", text=result)]
    
    elif name == "reload_apache":
        success, stdout, stderr = run_command(["sudo", "service", "apache2", "reload"])
        
        if success:
            result = "✓ Apache reloaded successfully"
        else:
            result = f"✗ Failed to reload Apache:\n{stderr}"
        
        return [TextContent(type="text", text=result)]
    
    elif name == "restart_apache":
        success, stdout, stderr = run_command(["sudo", "service", "apache2", "restart"])
        
        if success:
            result = "✓ Apache restarted successfully"
        else:
            result = f"✗ Failed to restart Apache:\n{stderr}"
        
        return [TextContent(type="text", text=result)]
    
    else:
        raise ValueError(f"Unknown tool: {name}")


# Starlette app endpoints
async def handle_sse(request: Request):
    """Handle SSE connection from MCP client."""
    global sse_transport
    
    # Create SSE transport for this connection
    sse = SseServerTransport("/messages")
    sse_transport = sse
    
    # Run the MCP server with this transport
    async with sse.connect_sse(
        request.scope,
        request.receive,
        request._send
    ) as streams:
        await mcp_server.run(
            streams[0],
            streams[1],
            mcp_server.create_initialization_options()
        )
    
    return Response()


async def handle_messages(request: Request):
    """Handle incoming messages from MCP client."""
    global sse_transport
    
    if sse_transport is None:
        return JSONResponse(
            {"error": "No SSE connection established"},
            status_code=400
        )
    
    await sse_transport.handle_post_message(
        request.scope,
        request.receive,
        request._send
    )
    
    return Response()


async def health_check(request: Request):
    """Health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "apache-mcp-server",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "authentication": "enabled"
    })


async def server_info(request: Request):
    """Server information endpoint."""
    return JSONResponse({
        "name": "Apache Management MCP Server",
        "version": "1.0.0",
        "transport": "SSE",
        "authentication": {
            "enabled": True,
            "method": "API Key",
            "header": API_KEY_HEADER
        },
        "endpoints": {
            "sse": "/sse",
            "messages": "/messages",
            "health": "/health"
        },
        "description": "MCP server for managing Apache web server configurations"
    })


# Create Starlette application
app = Starlette(
    routes=[
        Route("/", server_info),
        Route("/health", health_check),
        Route("/sse", handle_sse),
        Route("/messages", handle_messages, methods=["POST"]),
    ]
)

# Add authentication middleware FIRST (before CORS)
app.add_middleware(APIKeyAuthMiddleware)

# Add CORS middleware for remote access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("Apache Management MCP Server (SSE Transport)")
    print("=" * 60)
    print(f"Server starting on http://0.0.0.0:8000")
    print(f"SSE Endpoint: http://0.0.0.0:8000/sse")
    print(f"Messages Endpoint: http://0.0.0.0:8000/messages")
    print(f"Health Check: http://0.0.0.0:8000/health")
    print("=" * 60)
    print(f"\n Authentication: ENABLED")
    print(f"API Key: {API_KEY}")
    print(f"Header Required: {API_KEY_HEADER}: <your-api-key>")
    print("=" * 60)
    print("\nTo connect with MCP Inspector:")
    print(f"   npx @modelcontextprotocol/inspector http://YOUR_SERVER_IP:8000/sse")
    print(f"\n Set API key permanently:")
    print(f"   export MCP_API_KEY='{API_KEY}'")
    print(f"   python {sys.argv[0]}\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )