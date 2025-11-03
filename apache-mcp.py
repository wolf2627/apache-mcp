#!/usr/bin/env python3
"""
Apache Management MCP Server - Dual Transport (SSE + HTTP Streaming)

Supports both:
1. SSE Transport - /sse (GET) + /messages (POST)
2. HTTP Streaming - /message (GET + POST)
"""

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
from starlette.responses import Response, JSONResponse, StreamingResponse
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

if not API_KEY:
    API_KEY = secrets.token_urlsafe(32)
    print(f"\n⚠️  No MCP_API_KEY environment variable set!")
    print(f"🔑 Generated API Key: {API_KEY}")
    print(f"💡 Set it permanently: export MCP_API_KEY='{API_KEY}'\n")

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


# Tool execution helper
async def execute_tool(name: str, arguments: dict[str, Any]) -> dict:
    """Execute a tool and return JSON result."""
    
    if name == "list_available_sites":
        sites = list_sites(SITES_AVAILABLE)
        if not sites:
            return {"text": "No available sites found in /etc/apache2/sites-available"}
        
        result = "Available Apache Sites:\n\n"
        for site in sites:
            enabled = is_site_enabled(site)
            status = "✓ ENABLED" if enabled else "✗ disabled"
            result += f"  {status} - {site}\n"
        
        return {"text": result}
    
    elif name == "list_enabled_sites":
        sites = list_sites(SITES_ENABLED)
        if not sites:
            return {"text": "No enabled sites found in /etc/apache2/sites-enabled"}
        
        result = "Enabled Apache Sites:\n\n"
        for site in sites:
            result += f"  ✓ {site}\n"
        
        return {"text": result}
    
    elif name == "get_site_status":
        site_name = arguments.get("site_name", "")
        
        available = list_sites(SITES_AVAILABLE)
        if site_name not in available:
            return {"text": f"Error: Site '{site_name}' not found in sites-available"}
        
        enabled = is_site_enabled(site_name)
        config = get_site_config(site_name)
        
        result = f"Site: {site_name}\n"
        result += f"Status: {'ENABLED' if enabled else 'DISABLED'}\n"
        result += f"Available: Yes\n"
        result += f"Config Path: {SITES_AVAILABLE}/{site_name}\n"
        if enabled:
            result += f"Enabled Path: {SITES_ENABLED}/{site_name}\n"
        result += f"\nConfiguration:\n{'='*60}\n{config}\n"
        
        return {"text": result}
    
    elif name == "enable_site":
        site_name = arguments.get("site_name", "")
        reload = arguments.get("reload", True)
        
        available = list_sites(SITES_AVAILABLE)
        site_base = site_name.replace('.conf', '')
        
        site_exists = False
        for site in available:
            if site == site_name or site.replace('.conf', '') == site_base:
                site_exists = True
                break
        
        if not site_exists:
            return {"text": f"Error: Site '{site_name}' not found in sites-available"}
        
        if is_site_enabled(site_name):
            return {"text": f"Site '{site_name}' is already enabled"}
        
        success, stdout, stderr = run_command(["sudo", "a2ensite", site_name])
        
        if not success:
            return {"text": f"Error enabling site:\n{stderr}"}
        
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
        
        return {"text": result}
    
    elif name == "disable_site":
        site_name = arguments.get("site_name", "")
        reload = arguments.get("reload", True)
        
        if not is_site_enabled(site_name):
            return {"text": f"Site '{site_name}' is not enabled"}
        
        success, stdout, stderr = run_command(["sudo", "a2dissite", site_name])
        
        if not success:
            return {"text": f"Error disabling site:\n{stderr}"}
        
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
        
        return {"text": result}
    
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
        
        return {"text": result}
    
    elif name == "reload_apache":
        success, stdout, stderr = run_command(["sudo", "service", "apache2", "reload"])
        
        if success:
            result = "✓ Apache reloaded successfully"
        else:
            result = f"✗ Failed to reload Apache:\n{stderr}"
        
        return {"text": result}
    
    elif name == "restart_apache":
        success, stdout, stderr = run_command(["sudo", "service", "apache2", "restart"])
        
        if success:
            result = "✓ Apache restarted successfully"
        else:
            result = f"✗ Failed to restart Apache:\n{stderr}"
        
        return {"text": result}
    
    else:
        return {"error": f"Unknown tool: {name}"}


def get_tools_list():
    """Get list of tools for MCP responses."""
    return [
        {
            "name": "list_available_sites",
            "description": "List all available Apache site configurations",
            "inputSchema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "list_enabled_sites",
            "description": "List all enabled Apache site configurations",
            "inputSchema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "get_site_status",
            "description": "Get detailed status of a specific Apache site",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "site_name": {"type": "string", "description": "Site configuration file name"}
                },
                "required": ["site_name"]
            }
        },
        {
            "name": "enable_site",
            "description": "Enable an Apache site configuration",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "site_name": {"type": "string"},
                    "reload": {"type": "boolean", "default": True}
                },
                "required": ["site_name"]
            }
        },
        {
            "name": "disable_site",
            "description": "Disable an Apache site configuration",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "site_name": {"type": "string"},
                    "reload": {"type": "boolean", "default": True}
                },
                "required": ["site_name"]
            }
        },
        {
            "name": "test_config",
            "description": "Test Apache configuration for syntax errors",
            "inputSchema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "reload_apache",
            "description": "Reload Apache configuration",
            "inputSchema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "restart_apache",
            "description": "Restart Apache web server",
            "inputSchema": {"type": "object", "properties": {}, "required": []}
        }
    ]


def get_resources_list():
    """Get list of resources for MCP responses."""
    resources = []
    available_sites = list_sites(SITES_AVAILABLE)
    
    for site in available_sites:
        enabled = is_site_enabled(site)
        status = "enabled" if enabled else "disabled"
        
        resources.append({
            "uri": f"apache://sites-available/{site}",
            "name": f"{site} ({status})",
            "description": f"Apache site configuration - {status}",
            "mimeType": "text/plain"
        })
    
    return resources


# === TRANSPORT 1: SSE (Server-Sent Events) ===
async def handle_sse(request: Request):
    """Handle SSE connection from MCP client."""
    global sse_transport
    
    sse = SseServerTransport("/messages")
    sse_transport = sse
    
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


async def handle_sse_messages(request: Request):
    """Handle incoming messages for SSE transport."""
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


# === TRANSPORT 2: HTTP Streaming ===
async def handle_get_stream(request: Request):
    """
    Handle GET request for HTTP Streaming - server streams responses to client.
    """
    
    async def event_generator():
        """Generate server-to-client events."""
        try:
            # Send endpoint information
            endpoint_msg = {
                "jsonrpc": "2.0",
                "method": "endpoint",
                "params": {
                    "uri": str(request.url).replace("/message", "")
                }
            }
            yield f"{json.dumps(endpoint_msg)}\n"
            
            # Keep connection alive
            while True:
                await asyncio.sleep(1)
                
        except asyncio.CancelledError:
            pass
    
    return StreamingResponse(
        event_generator(),
        media_type="application/json",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


async def handle_post_message(request: Request):
    """
    Handle POST request for HTTP Streaming - client sends requests to server.
    """
    try:
        body = await request.json()
        
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id")
        
        print(f"📨 Received: {method}")
        
        # Handle initialize
        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "resources": {}
                    },
                    "serverInfo": {
                        "name": "apache-manager",
                        "version": "1.0.0"
                    }
                }
            }
            return JSONResponse(response)
        
        # Handle notifications/initialized
        elif method == "notifications/initialized":
            return Response(status_code=200)
        
        # Handle tools/list
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": get_tools_list()
                }
            }
            return JSONResponse(response)
        
        # Handle tools/call
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            if not tool_name:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": "Missing tool name"
                    }
                }, status_code=400)
            
            result = await execute_tool(tool_name, arguments)
            
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": result.get("text", json.dumps(result))
                        }
                    ]
                }
            }
            return JSONResponse(response)
        
        # Handle resources/list
        elif method == "resources/list":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "resources": get_resources_list()
                }
            }
            return JSONResponse(response)
        
        # Handle resources/read
        elif method == "resources/read":
            uri = params.get("uri", "")
            
            if not uri.startswith("apache://sites-available/"):
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": f"Unknown resource URI: {uri}"
                    }
                }, status_code=400)
            
            site_name = uri.replace("apache://sites-available/", "")
            config_content = get_site_config(site_name)
            
            if not config_content:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": f"Site configuration not found: {site_name}"
                    }
                }, status_code=404)
            
            enabled = is_site_enabled(site_name)
            status = "ENABLED" if enabled else "DISABLED"
            
            content = f"# Apache Site: {site_name}\n# Status: {status}\n\n{config_content}"
            
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/plain",
                            "text": content
                        }
                    ]
                }
            }
            return JSONResponse(response)
        
        else:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }, status_code=404)
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }, status_code=500)


# MCP Server handlers (for SSE transport)
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
    tools_data = get_tools_list()
    return [
        Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["inputSchema"]
        ) for t in tools_data
    ]


@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool execution requests."""
    result = await execute_tool(name, arguments)
    return [TextContent(type="text", text=result.get("text", json.dumps(result)))]


# Info endpoints
async def health_check(request: Request):
    """Health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "apache-mcp-server",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "authentication": "enabled",
        "transports": ["sse", "http-streaming"]
    })


async def server_info(request: Request):
    """Server information endpoint."""
    return JSONResponse({
        "name": "Apache Management MCP Server",
        "version": "1.0.0",
        "transports": {
            "sse": {
                "endpoints": {
                    "sse": "/sse (GET)",
                    "messages": "/messages (POST)"
                },
                "description": "Server-Sent Events transport for Claude Desktop"
            },
            "http-streaming": {
                "endpoint": "/message (GET + POST)",
                "description": "HTTP Streaming transport for MCP Inspector"
            }
        },
        "authentication": {
            "enabled": True,
            "method": "API Key",
            "header": API_KEY_HEADER
        }
    })


# Create Starlette application
app = Starlette(
    routes=[
        Route("/", server_info),
        Route("/health", health_check),
        # SSE Transport
        Route("/sse", handle_sse, methods=["GET"]),
        Route("/messages", handle_sse_messages, methods=["POST"]),
        # HTTP Streaming Transport
        Route("/message", handle_get_stream, methods=["GET"]),
        Route("/message", handle_post_message, methods=["POST"]),
    ]
)

# Add authentication middleware
app.add_middleware(APIKeyAuthMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


if __name__ == "__main__":
    print("=" * 70)
    print("Apache Management MCP Server - Dual Transport")
    print("=" * 70)
    print(f"Server: http://0.0.0.0:8000")
    print("\n Transport Options:")
    print("   1. SSE (Server-Sent Events):")
    print("      - GET  /sse      - Open SSE connection")
    print("      - POST /messages - Send messages")
    print("\n   2. HTTP Streaming:")
    print("      - GET  /message  - Server streams to client")
    print("      - POST /message  - Client sends to server")
    print("=" * 70)
    print(f"\ Authentication: ENABLED")
    print(f"🔑 API Key: {API_KEY}")
    print(f"Header: {API_KEY_HEADER}")
    print("=" * 70 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")