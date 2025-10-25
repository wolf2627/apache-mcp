#!/usr/bin/env python3
"""
Apache Management REST API Server

A FastAPI-based REST API server for managing Apache web server on Linux systems.
Provides endpoints for viewing, enabling, and disabling site configurations.
"""

from fastapi import FastAPI, HTTPException, Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import subprocess
import os
from pathlib import Path
import uvicorn
import secrets

# Apache configuration paths
SITES_AVAILABLE = "/etc/apache2/sites-available"
SITES_ENABLED = "/etc/apache2/sites-enabled"

# Security: Generate or set your API key
API_KEY = os.getenv("APACHE_API_KEY", "apache-mcp")

app = FastAPI(
    title="Apache Management API",
    description="REST API for managing Apache web server configurations",
    version="1.0.0"
)

# Pydantic models
class SiteInfo(BaseModel):
    name: str
    enabled: bool
    available: bool

class SiteDetail(BaseModel):
    name: str
    enabled: bool
    available: bool
    config_path: str
    enabled_path: Optional[str] = None
    configuration: str

class SiteAction(BaseModel):
    site_name: str
    reload: bool = True

class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Optional[dict] = None

class ConfigTestResponse(BaseModel):
    success: bool
    syntax_ok: bool
    output: str
    errors: str


def verify_api_key(x_api_key: str = Header(...)):
    """Verify API key from request header."""
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return x_api_key


def run_command(cmd: List[str]) -> tuple[bool, str, str]:
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


def list_sites(directory: str) -> List[str]:
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


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Apache Management API",
        "version": "1.0.0",
        "endpoints": {
            "GET /health": "Health check",
            "GET /sites/available": "List available sites",
            "GET /sites/enabled": "List enabled sites",
            "GET /sites/{site_name}": "Get site details",
            "POST /sites/enable": "Enable a site",
            "POST /sites/disable": "Disable a site",
            "GET /config/test": "Test Apache configuration",
            "POST /apache/reload": "Reload Apache",
            "POST /apache/restart": "Restart Apache"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "apache-management-api"}


@app.get("/sites/available", response_model=List[SiteInfo])
async def list_available_sites(x_api_key: str = Header(...)):
    """List all available Apache site configurations."""
    verify_api_key(x_api_key)
    
    sites = list_sites(SITES_AVAILABLE)
    result = []
    
    for site in sites:
        result.append(SiteInfo(
            name=site,
            enabled=is_site_enabled(site),
            available=True
        ))
    
    return result


@app.get("/sites/enabled", response_model=List[str])
async def list_enabled_sites(x_api_key: str = Header(...)):
    """List all enabled Apache site configurations."""
    verify_api_key(x_api_key)
    
    sites = list_sites(SITES_ENABLED)
    return sites


@app.get("/sites/{site_name}", response_model=SiteDetail)
async def get_site_details(site_name: str, x_api_key: str = Header(...)):
    """Get detailed information about a specific site."""
    verify_api_key(x_api_key)
    
    available = list_sites(SITES_AVAILABLE)
    if site_name not in available:
        raise HTTPException(
            status_code=404,
            detail=f"Site '{site_name}' not found in sites-available"
        )
    
    enabled = is_site_enabled(site_name)
    config = get_site_config(site_name)
    
    return SiteDetail(
        name=site_name,
        enabled=enabled,
        available=True,
        config_path=f"{SITES_AVAILABLE}/{site_name}",
        enabled_path=f"{SITES_ENABLED}/{site_name}" if enabled else None,
        configuration=config
    )


@app.post("/sites/enable", response_model=ApiResponse)
async def enable_site(action: SiteAction, x_api_key: str = Header(...)):
    """Enable an Apache site configuration."""
    verify_api_key(x_api_key)
    
    site_name = action.site_name
    reload = action.reload
    
    # Check if site exists
    available = list_sites(SITES_AVAILABLE)
    site_base = site_name.replace('.conf', '')
    
    site_exists = False
    for site in available:
        if site == site_name or site.replace('.conf', '') == site_base:
            site_exists = True
            break
    
    if not site_exists:
        raise HTTPException(
            status_code=404,
            detail=f"Site '{site_name}' not found in sites-available"
        )
    
    # Check if already enabled
    if is_site_enabled(site_name):
        return ApiResponse(
            success=True,
            message=f"Site '{site_name}' is already enabled",
            data={"already_enabled": True}
        )
    
    # Enable the site
    success, stdout, stderr = run_command(["sudo", "a2ensite", site_name])
    
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Error enabling site: {stderr}"
        )
    
    message = f"Successfully enabled site: {site_name}"
    data = {"stdout": stdout}
    
    # Reload Apache if requested
    if reload:
        reload_success, reload_out, reload_err = run_command(
            ["sudo", "service", "apache2", "reload"]
        )
        if reload_success:
            message += "\nApache configuration reloaded successfully"
            data["reloaded"] = True
        else:
            message += f"\nWarning: Failed to reload Apache: {reload_err}"
            data["reloaded"] = False
            data["reload_error"] = reload_err
    else:
        message += "\nApache not reloaded. Call /apache/reload to apply changes."
        data["reloaded"] = False
    
    return ApiResponse(success=True, message=message, data=data)


@app.post("/sites/disable", response_model=ApiResponse)
async def disable_site(action: SiteAction, x_api_key: str = Header(...)):
    """Disable an Apache site configuration."""
    verify_api_key(x_api_key)
    
    site_name = action.site_name
    reload = action.reload
    
    # Check if site is enabled
    if not is_site_enabled(site_name):
        return ApiResponse(
            success=True,
            message=f"Site '{site_name}' is not enabled",
            data={"already_disabled": True}
        )
    
    # Disable the site
    success, stdout, stderr = run_command(["sudo", "a2dissite", site_name])
    
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Error disabling site: {stderr}"
        )
    
    message = f"Successfully disabled site: {site_name}"
    data = {"stdout": stdout}
    
    # Reload Apache if requested
    if reload:
        reload_success, reload_out, reload_err = run_command(
            ["sudo", "service", "apache2", "reload"]
        )
        if reload_success:
            message += "\nApache configuration reloaded successfully"
            data["reloaded"] = True
        else:
            message += f"\nWarning: Failed to reload Apache: {reload_err}"
            data["reloaded"] = False
            data["reload_error"] = reload_err
    else:
        message += "\nApache not reloaded. Call /apache/reload to apply changes."
        data["reloaded"] = False
    
    return ApiResponse(success=True, message=message, data=data)


@app.get("/config/test", response_model=ConfigTestResponse)
async def test_config(x_api_key: str = Header(...)):
    """Test Apache configuration for syntax errors."""
    verify_api_key(x_api_key)
    
    success, stdout, stderr = run_command(["sudo", "apache2ctl", "configtest"])
    
    return ConfigTestResponse(
        success=success,
        syntax_ok=success,
        output=stdout,
        errors=stderr
    )


@app.post("/apache/reload", response_model=ApiResponse)
async def reload_apache(x_api_key: str = Header(...)):
    """Reload Apache configuration without dropping connections."""
    verify_api_key(x_api_key)
    
    success, stdout, stderr = run_command(["sudo", "service", "apache2", "reload"])
    
    if success:
        return ApiResponse(
            success=True,
            message="Apache reloaded successfully",
            data={"stdout": stdout}
        )
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload Apache: {stderr}"
        )


@app.post("/apache/restart", response_model=ApiResponse)
async def restart_apache(x_api_key: str = Header(...)):
    """Restart Apache web server (drops all connections)."""
    verify_api_key(x_api_key)
    
    success, stdout, stderr = run_command(["sudo", "service", "apache2", "restart"])
    
    if success:
        return ApiResponse(
            success=True,
            message="Apache restarted successfully",
            data={"stdout": stdout}
        )
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to restart Apache: {stderr}"
        )


if __name__ == "__main__":
    # Generate a secure API key if using default
    if API_KEY == "apache-mcp":
        print("⚠️  WARNING: Using default API key. Set APACHE_API_KEY environment variable!")
        print(f"   Suggested key: {secrets.token_urlsafe(32)}")
    
    print(f"🔑 API Key: {API_KEY}")
    print("📚 API Docs: http://0.0.0.0:8000/docs")
    print("🔧 Management: Include 'X-API-Key: {apache-mcp}' header in all requests")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)