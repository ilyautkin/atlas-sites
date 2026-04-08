import io
import os
import re
import json
import tarfile
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, List
from io import StringIO

import paramiko

from mcp.server.fastmcp import FastMCP

ATLAS_API_URL = "https://atlas.heibel.nl/api/sites"
DEFAULT_SSH_PORT = 22622
HTTPDOCS_PATH = "httpdocs"

mcp = FastMCP("Atlas Sites Resolver", json_response=True)

# In-memory credentials cache: domain -> site_data
_credentials_cache: Dict[str, Dict[str, Any]] = {}

# In-memory backup tracking: set of "domain:path" strings for files that have backups in this session
_backups_cache: set = set()


def extract_domain(text: str) -> Optional[str]:
    m = re.search(r"(https?://)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?:/|\\b)", text)
    if not m:
        return None
    return m.group(2).lower()


def atlas_search(search: str) -> Any:
    token = os.environ.get("ATLAS_TOKEN")
    if not token:
        return {"ok": False, "error": "ATLAS_TOKEN is not set (env var)."}

    params = urllib.parse.urlencode({"search": search})
    url = f"{ATLAS_API_URL}?{params}"

    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}", "body": body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_site_credentials(domain: str) -> Dict[str, Any]:
    """Get site credentials from cache or fetch from Atlas API."""
    domain = domain.lower()

    if domain in _credentials_cache:
        return {"ok": True, "data": _credentials_cache[domain], "cached": True}

    result = atlas_search(domain)

    if isinstance(result, dict) and result.get("ok") is False:
        return result

    # Extract site data from API response
    sites = result.get("data", [])
    if not sites:
        return {"ok": False, "error": f"Site not found: {domain}"}

    site = sites[0]
    _credentials_cache[domain] = site

    return {"ok": True, "data": site, "cached": False}


def get_ssh_connection(domain: str) -> tuple[Optional[paramiko.SSHClient], Optional[Dict[str, Any]]]:
    """Create SSH connection to site server."""
    creds = get_site_credentials(domain)

    if not creds.get("ok"):
        return None, creds

    site = creds["data"]
    server = site.get("server", {})
    hostname = server.get("name") if isinstance(server, dict) else None

    if not hostname:
        return None, {"ok": False, "error": "No server hostname found"}

    username = site.get("user")
    password = site.get("password")
    port = site.get("port", DEFAULT_SSH_PORT)

    if not username or not password:
        site_id = site.get("id")
        edit_url = f"https://atlas.heibel.nl/?edit={site_id}" if site_id else None
        error = {"ok": False, "error": "SSH credentials (user/password) are not set for this site."}
        if edit_url:
            error["edit_url"] = edit_url
            error["message"] = f"Fill in the credentials here and retry: {edit_url}"
        return None, error

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=hostname,
            port=port,
            username=username,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False
        )
        return client, None
    except Exception as e:
        return None, {"ok": False, "error": f"SSH connection failed: {str(e)}"}


def ssh_exec(domain: str, command: str) -> Dict[str, Any]:
    """Execute command via SSH and return result."""
    client, error = get_ssh_connection(domain)

    if error:
        return error

    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace")
        err_output = stderr.read().decode("utf-8", errors="replace")

        return {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "stdout": output,
            "stderr": err_output
        }
    except Exception as e:
        return {"ok": False, "error": f"Command execution failed: {str(e)}"}
    finally:
        client.close()


def ensure_backup(domain: str, path: str) -> Dict[str, Any]:
    """
    Ensure a backup exists for the file. Creates one if needed.
    Returns info about backup status.
    """
    cache_key = f"{domain.lower()}:{path}"

    # If we already handled backup in this session, skip
    if cache_key in _backups_cache:
        return {"ok": True, "backup_created": False, "reason": "already_handled_this_session"}

    full_path = f"{HTTPDOCS_PATH}/{path.lstrip('/')}"
    backup_path = f"{full_path}.backup"

    # Check if backup already exists on server
    check_result = ssh_exec(domain, f"test -f '{backup_path}' && echo 'exists'")
    backup_exists = "exists" in check_result.get("stdout", "")

    if backup_exists:
        # Backup exists from previous session, don't overwrite
        _backups_cache.add(cache_key)
        return {"ok": True, "backup_created": False, "reason": "backup_already_exists"}

    # Create backup
    result = ssh_exec(domain, f"cp '{full_path}' '{backup_path}'")

    if not result.get("ok"):
        return {"ok": False, "error": f"Failed to create backup: {result.get('stderr', result.get('error', 'Unknown error'))}"}

    _backups_cache.add(cache_key)
    return {"ok": True, "backup_created": True, "backup_path": f"{path}.backup"}


# ============ Original tools ============

@mcp.tool()
def resolve_site_from_text(text: str) -> Dict[str, Any]:
    """Extract domain from text and search in Atlas database."""
    domain = extract_domain(text)
    if not domain:
        return {"ok": True, "found": False, "reason": "No domain found in text.", "matches": []}

    data = atlas_search(domain)
    if isinstance(data, dict) and data.get("ok") is False:
        return data

    return {"ok": True, "found": True, "query": domain, "matches": data}


@mcp.tool()
def resolve_site(search: str) -> Dict[str, Any]:
    """Search for a site in Atlas database."""
    data = atlas_search(search)
    if isinstance(data, dict) and data.get("ok") is False:
        return data
    return {"ok": True, "query": search, "matches": data}


# ============ New SSH file tools ============

@mcp.tool()
def detect_theme(domain: str) -> Dict[str, Any]:
    """
    Detect which MODX theme is used on the site.
    Returns 'old' (modx3-circle), 'new' (theme), or 'unknown'.

    Old theme has: httpdocs/assets/scss/override.scss
    New theme has: httpdocs/assets/scss/style.scss
    """
    # Check for old theme
    old_check = ssh_exec(domain, f"test -f {HTTPDOCS_PATH}/assets/scss/override.scss && echo 'exists'")
    if old_check.get("ok") and "exists" in old_check.get("stdout", ""):
        return {"ok": True, "theme": "old", "theme_name": "modx3-circle"}

    # Check for new theme
    new_check = ssh_exec(domain, f"test -f {HTTPDOCS_PATH}/assets/scss/style.scss && echo 'exists'")
    if new_check.get("ok") and "exists" in new_check.get("stdout", ""):
        return {"ok": True, "theme": "new", "theme_name": "theme"}

    return {"ok": True, "theme": "unknown", "theme_name": None}


@mcp.tool()
def read_file(domain: str, path: str) -> Dict[str, Any]:
    """
    Read file content from the site server.
    Path is relative to httpdocs/ (e.g., 'assets/scss/style.scss').
    """
    full_path = f"{HTTPDOCS_PATH}/{path.lstrip('/')}"
    result = ssh_exec(domain, f"cat '{full_path}'")

    if not result.get("ok"):
        # Check if file doesn't exist
        if "No such file" in result.get("stderr", ""):
            return {"ok": False, "error": f"File not found: {path}"}
        return result

    return {"ok": True, "path": path, "content": result.get("stdout", "")}


@mcp.tool()
def write_file(domain: str, path: str, content: str) -> Dict[str, Any]:
    """
    Write content to a file on the site server.
    Path is relative to httpdocs/ (e.g., 'assets/scss/style.scss').
    Automatically creates a .backup before first write in session.
    """
    # Ensure backup exists before writing
    backup_result = ensure_backup(domain, path)
    if not backup_result.get("ok"):
        return backup_result

    full_path = f"{HTTPDOCS_PATH}/{path.lstrip('/')}"

    # Use heredoc to write content
    command = f"cat > '{full_path}' << 'EOFCONTENT'\n{content}\nEOFCONTENT"

    result = ssh_exec(domain, command)

    if not result.get("ok"):
        return {"ok": False, "error": f"Failed to write file: {result.get('stderr', result.get('error', 'Unknown error'))}"}

    response = {"ok": True, "path": path, "message": "File written successfully"}

    # Include backup info if it was just created
    if backup_result.get("backup_created"):
        response["backup_created"] = True
        response["backup_path"] = backup_result.get("backup_path")

    return response


@mcp.tool()
def file_exists(domain: str, path: str) -> Dict[str, Any]:
    """
    Check if a file exists on the site server.
    Path is relative to httpdocs/.
    """
    full_path = f"{HTTPDOCS_PATH}/{path.lstrip('/')}"
    result = ssh_exec(domain, f"test -f '{full_path}' && echo 'exists' || echo 'not_found'")

    if not result.get("ok") and "exists" not in result.get("stdout", ""):
        return result

    exists = "exists" in result.get("stdout", "")
    return {"ok": True, "path": path, "exists": exists}


@mcp.tool()
def list_files(domain: str, path: str, pattern: str = "*") -> Dict[str, Any]:
    """
    List files in a directory on the site server.
    Path is relative to httpdocs/.
    Pattern is a glob pattern (default: '*').
    """
    full_path = f"{HTTPDOCS_PATH}/{path.lstrip('/')}"
    result = ssh_exec(domain, f"ls -la '{full_path}'/{pattern} 2>/dev/null || ls -la '{full_path}' 2>/dev/null")

    if not result.get("ok") and not result.get("stdout"):
        return {"ok": False, "error": f"Directory not found or empty: {path}"}

    return {"ok": True, "path": path, "pattern": pattern, "listing": result.get("stdout", "")}


@mcp.tool()
def clear_cache(domain: str) -> Dict[str, Any]:
    """
    Clear MODX cache by removing contents of:
    - core/cache/ (main MODX cache)
    - assets/components/modxminify/cache/ (modxminify cache)
    Use this after making changes to site files.
    """
    core_cache = f"{HTTPDOCS_PATH}/core/cache"
    minify_cache = f"{HTTPDOCS_PATH}/assets/components/modxminify/cache"

    # Clear both cache directories
    result = ssh_exec(domain, f"find '{core_cache}' -mindepth 1 -delete 2>/dev/null; find '{minify_cache}' -mindepth 1 -delete 2>/dev/null; echo 'done'")

    if "done" not in result.get("stdout", ""):
        return {"ok": False, "error": f"Failed to clear cache: {result.get('stderr', result.get('error', 'Unknown error'))}"}

    return {"ok": True, "message": "MODX cache cleared (core + modxminify)"}


@mcp.tool()
def delete_backups(domain: str) -> Dict[str, Any]:
    """
    Delete all .backup files that were created during this session.
    Use this after confirming changes are working correctly.
    """
    domain_lower = domain.lower()

    # Find all backups for this domain from cache
    domain_backups = [key for key in _backups_cache if key.startswith(f"{domain_lower}:")]

    if not domain_backups:
        return {"ok": True, "deleted": [], "message": "No backups to delete for this domain"}

    deleted = []
    errors = []

    for cache_key in domain_backups:
        # Extract path from cache key (format: "domain:path")
        path = cache_key.split(":", 1)[1]
        backup_path = f"{HTTPDOCS_PATH}/{path.lstrip('/')}.backup"

        result = ssh_exec(domain, f"rm -f '{backup_path}'")

        if result.get("ok") or result.get("exit_code") == 0:
            deleted.append(f"{path}.backup")
            _backups_cache.discard(cache_key)
        else:
            errors.append(f"{path}.backup: {result.get('stderr', 'Unknown error')}")

    response = {
        "ok": len(errors) == 0,
        "deleted": deleted,
        "message": f"Deleted {len(deleted)} backup(s)"
    }

    if errors:
        response["errors"] = errors

    return response


@mcp.tool()
def deploy_folder(domain: str, local_path: str, remote_path: str) -> Dict[str, Any]:
    """
    Deploy a local folder to the site server via tar stream over SSH.
    The folder is compressed locally and extracted on the remote side in one operation.

    Args:
        domain: Site domain (Atlas will resolve SSH credentials).
        local_path: Absolute path to local folder to deploy.
        remote_path: Destination path on server relative to user home (e.g. 'httpdocs/core/components').

    The folder itself is placed inside remote_path, so:
        local_path=/tmp/commerce_defrysling + remote_path=httpdocs/core/components
        → httpdocs/core/components/commerce_defrysling/
    """
    local_path = os.path.abspath(local_path)

    if not os.path.isdir(local_path):
        return {"ok": False, "error": f"Local path is not a directory: {local_path}"}

    folder_name = os.path.basename(local_path)

    # Build tar.gz in memory, skip system junk files
    buf = io.BytesIO()
    try:
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for dirpath, dirnames, filenames in os.walk(local_path):
                # Skip .git, __pycache__, .venv in-place
                dirnames[:] = [
                    d for d in dirnames
                    if d not in {".git", "__pycache__", ".venv", "node_modules"}
                ]
                for filename in filenames:
                    if filename.startswith("._") or filename == ".DS_Store":
                        continue
                    full_file = os.path.join(dirpath, filename)
                    arcname = os.path.join(
                        folder_name,
                        os.path.relpath(full_file, local_path)
                    )
                    tar.add(full_file, arcname=arcname)
    except Exception as e:
        return {"ok": False, "error": f"Failed to create tar archive: {str(e)}"}

    archive_size = buf.tell()
    buf.seek(0)

    # Connect via SSH
    client, error = get_ssh_connection(domain)
    if error:
        return error

    try:
        remote_path = remote_path.rstrip("/")
        cmd = (
            f"mkdir -p '{remote_path}' && "
            f"tar -xzf - -C '{remote_path}' && "
            f"find '{remote_path}/{folder_name}' -name '._*' -type f -delete"
        )
        stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
        stdin.write(buf.read())
        stdin.channel.shutdown_write()

        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")

        if exit_code != 0:
            return {
                "ok": False,
                "error": f"Remote tar failed (exit {exit_code})",
                "stderr": err,
                "stdout": out,
            }

        return {
            "ok": True,
            "folder": folder_name,
            "remote_path": f"{remote_path}/{folder_name}",
            "archive_bytes": archive_size,
            "message": f"Deployed {folder_name} to {remote_path}/{folder_name}",
        }
    except Exception as e:
        return {"ok": False, "error": f"Deployment failed: {str(e)}"}
    finally:
        client.close()


# ============ Console script tools ============

@mcp.tool()
def download_fonts(domain: str, url: str) -> Dict[str, Any]:
    """
    Download Google Fonts to local assets/fonts/ on the site server.
    Runs storage/core/components/console/files/global/downloadfonts.php via CLI.
    Returns the CSS filename and the <script> tag to insert in head.tpl.
    """
    script_path = f"{HTTPDOCS_PATH}/core/components/console/files/global/downloadfonts.php"
    result = ssh_exec(domain, f"php {script_path} {url!r}")

    if not result.get("ok"):
        return {"ok": False, "error": result.get("stderr") or result.get("error", "Script error")}

    output = result.get("stdout", "")

    # Parse output: first line is "OK:<cssFileName>", second is the <script> tag
    lines = [l for l in output.strip().splitlines() if l.strip()]
    css_file = None
    script_tag = None

    for line in lines:
        if line.startswith("OK:"):
            css_file = line[3:].strip()
        elif "<script" in line:
            script_tag = line.strip()

    return {
        "ok": True,
        "css_file": css_file,
        "script_tag": script_tag,
        "raw_output": output.strip(),
    }


# ============ ClientConfig tools ============

def mysql_exec(domain: str, sql: str) -> Dict[str, Any]:
    """Execute a MySQL query using DB credentials from Atlas. SQL is passed via stdin to avoid shell quoting issues."""
    creds = get_site_credentials(domain)
    if not creds.get("ok"):
        return creds

    site = creds["data"]
    dbname = site.get("dbname")
    dbuser = site.get("dbuser")
    dbpass = site.get("dbpass", "")

    if not dbname or not dbuser:
        return {"ok": False, "error": "DB credentials (dbname/dbuser) not set in Atlas for this site."}

    client, error = get_ssh_connection(domain)
    if error:
        return error

    try:
        pass_arg = f"-p{dbpass}" if dbpass else ""
        command = f"mysql -h 127.0.0.1 -u {dbuser} {pass_arg} {dbname}"
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        stdin.write(sql.encode("utf-8"))
        stdin.channel.shutdown_write()

        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace")
        err_output = stderr.read().decode("utf-8", errors="replace")

        return {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "stdout": output,
            "stderr": err_output,
        }
    except Exception as e:
        return {"ok": False, "error": f"MySQL execution failed: {str(e)}"}
    finally:
        client.close()


@mcp.tool()
def set_client_config_bulk(domain: str, settings: Dict[str, str]) -> Dict[str, Any]:
    """
    Update multiple ClientConfig settings in one MySQL call.
    settings is a dict of {key: value} pairs.
    """
    if not settings:
        return {"ok": False, "error": "settings dict is empty"}

    statements = []
    for key, value in settings.items():
        safe_key = key.replace("'", "\\'")
        safe_value = value.replace("'", "\\'")
        statements.append(f"UPDATE modx_clientconfig_setting SET value='{safe_value}' WHERE `key`='{safe_key}';")

    sql = " ".join(statements)
    result = mysql_exec(domain, sql)

    if not result.get("ok"):
        return {"ok": False, "error": result.get("stderr") or result.get("error", "MySQL error")}

    return {
        "ok": True,
        "updated": list(settings.keys()),
        "count": len(settings),
    }


@mcp.tool()
def set_client_config(domain: str, key: str, value: str) -> Dict[str, Any]:
    """
    Update a ClientConfig setting (cgSetting) in the MODX database.
    The setting must already exist (inserted by theme installer).
    """
    safe_value = value.replace("'", "\\'")
    safe_key = key.replace("'", "\\'")

    sql = f"UPDATE modx_clientconfig_setting SET value='{safe_value}' WHERE `key`='{safe_key}';"

    result = mysql_exec(domain, sql)

    if not result.get("ok"):
        return {"ok": False, "error": result.get("stderr") or result.get("error", "MySQL error")}

    check_sql = f"SELECT `key`, value FROM modx_clientconfig_setting WHERE `key`='{safe_key}';"
    check = mysql_exec(domain, check_sql)

    return {
        "ok": True,
        "key": key,
        "value": value,
        "current": check.get("stdout", "").strip() if check.get("ok") else None,
    }


@mcp.tool()
def get_client_config(domain: str, key: str) -> Dict[str, Any]:
    """
    Read a ClientConfig setting (cgSetting) from the MODX database.
    Pass key='*' to get all settings.
    """
    prefix = get_modx_table_prefix(domain)
    table = f"{prefix}cgSetting"

    if key == "*":
        sql = "SELECT `key`, value FROM modx_clientconfig_setting;"
    else:
        safe_key = key.replace("'", "\\'")
        sql = f"SELECT `key`, value FROM modx_clientconfig_setting WHERE `key`='{safe_key}';"

    result = mysql_exec(domain, sql)

    if not result.get("ok"):
        return {"ok": False, "error": result.get("stderr") or result.get("error", "MySQL error")}

    return {
        "ok": True,
        "key": key,
        "result": result.get("stdout", "").strip(),
    }


if __name__ == "__main__":
    mcp.run()
