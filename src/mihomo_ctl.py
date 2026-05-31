# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""mihomo-ctl: standalone CLI controller for mihomo.

Config directory: ~/.local/share/mihomo/
  config.yaml     - base config (ports, dns, tun)
  profiles.yaml   - subscription list
  profiles/       - downloaded subscription files
  script.js       - global enhancement script
  generated.yaml  - final generated config for mihomo
"""

import gzip
import http.client
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import yaml

# ─── Paths ────────────────────────────────────────────────────────────────────

USE_VERGE = "--verge" in sys.argv
if USE_VERGE:
    sys.argv.remove("--verge")

VERGE_DIR = os.path.expanduser(
    "~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"
)
OWN_DIR = os.path.expanduser("~/.local/share/mihomo")

BASE_DIR = VERGE_DIR if USE_VERGE else OWN_DIR

if USE_VERGE:
    CONFIG_YAML = os.path.join(VERGE_DIR, "config.yaml")
    PROFILES_YAML = os.path.join(VERGE_DIR, "profiles.yaml")
    PROFILES_DIR = os.path.join(VERGE_DIR, "profiles")
    GLOBAL_SCRIPT = os.path.join(PROFILES_DIR, "Script.js")
    GENERATED_YAML = os.path.join(VERGE_DIR, "clash-verge.yaml")
else:
    CONFIG_YAML = os.path.join(OWN_DIR, "config.yaml")
    PROFILES_YAML = os.path.join(OWN_DIR, "profiles.yaml")
    PROFILES_DIR = os.path.join(OWN_DIR, "profiles")
    GLOBAL_SCRIPT = os.path.join(OWN_DIR, "script.js")
    GENERATED_YAML = os.path.join(OWN_DIR, "generated.yaml")

# Socket: prefer Verge's (if --verge or exists), then own
SOCK_VERGE = "/tmp/verge/verge-mihomo.sock"
SOCK_OWN = "/tmp/mihomo-ctl.sock"


def _find_sock():
    """Find available unix socket, return None if not found."""
    if USE_VERGE:
        if os.path.exists(SOCK_VERGE):
            return SOCK_VERGE
    else:
        if os.path.exists(SOCK_OWN):
            return SOCK_OWN
        if os.path.exists(SOCK_VERGE):
            return SOCK_VERGE
    # Fallback: try reading from generated config
    if os.path.exists(GENERATED_YAML):
        with open(GENERATED_YAML) as f:
            cfg = yaml.safe_load(f) or {}
        sock = cfg.get("external-controller-unix", "")
        if sock and os.path.exists(sock):
            return sock
    return None


def get_sock():
    """Find available unix socket, exit if not found."""
    sock = _find_sock()
    if sock:
        return sock
    print("Error: no mihomo socket found. Is mihomo running?")
    sys.exit(1)


# ─── Unix socket HTTP client ──────────────────────────────────────────────────


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def api(path, method="GET", data=None):
    sock = get_sock()
    conn = UnixHTTPConnection(sock)
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    content = resp.read().decode()
    conn.close()
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return None


def api_stream(path):
    """Return a streaming response for traffic/logs/memory."""
    sock = get_sock()
    conn = UnixHTTPConnection(sock)
    conn.request("GET", path)
    return conn, conn.getresponse()


def urlencode(s):
    return urllib.parse.quote(s, safe="")


def reload_config():
    """Reload mihomo with generated config."""
    api("/configs?force=true", "PUT", {"path": GENERATED_YAML, "payload": ""})


# ─── Commands ─────────────────────────────────────────────────────────────────


def cmd_status():
    ver = api("/version")
    cfg = api("/configs")
    print("=== Mihomo Status ===")
    print(f"  Version:    {ver.get('version', '?')}")
    print(f"  Mode:       {cfg['mode']}")
    print(f"  Mixed Port: {cfg['mixed-port']}")
    print(f"  Allow LAN:  {cfg['allow-lan']}")
    print(f"  Log Level:  {cfg['log-level']}")
    print(f"  TUN:        {cfg['tun']['enable']}")
    print(f"  IPv6:       {cfg['ipv6']}")
    print(f"  Socket:     {get_sock()}")


def cmd_mode(new_mode=None):
    if new_mode:
        api("/configs", "PATCH", {"mode": new_mode})
        print(f"Mode switched to: {new_mode}")
    else:
        cfg = api("/configs")
        print(cfg["mode"])


def cmd_groups():
    data = api("/group")
    for g in data.get("proxies", []):
        t = g["type"]
        name = g["name"]
        now = g.get("now", "N/A")
        print(f"  [{t:10s}] {name:20s} => {now}")


def cmd_list(group):
    data = api(f"/proxies/{urlencode(group)}")
    if isinstance(data, str) or "message" in (data or {}):
        print(f"Error: group '{group}' not found")
        sys.exit(1)
    now = data.get("now", "")
    for i, n in enumerate(data.get("all", [])):
        marker = " ◀" if n == now else ""
        print(f"  {i+1:2d}. {n}{marker}")


def cmd_switch(group, proxy):
    api(f"/proxies/{urlencode(group)}", "PUT", {"name": proxy})
    print(f"Switched [{group}] => {proxy}")


def cmd_delay(proxy, url="https://www.gstatic.com/generate_204", timeout="5000"):
    result = api(
        f"/proxies/{urlencode(proxy)}/delay?url={url}&timeout={timeout}"
    )
    if isinstance(result, dict) and "delay" in result:
        print(f"  {proxy}: {result['delay']}ms")
    else:
        print(json.dumps(result, indent=2))


def cmd_gdelay(group, url="https://www.gstatic.com/generate_204", timeout="5000"):
    result = api(f"/group/{urlencode(group)}/delay?url={url}&timeout={timeout}")
    if not isinstance(result, dict):
        print(f"Error: {result}")
        return
    alive = [(n, d) for n, d in result.items() if isinstance(d, int) and d > 0]
    alive.sort(key=lambda x: x[1])
    for name, delay in alive:
        print(f"  {delay:5d}ms  {name}")
    dead = [(n, d) for n, d in result.items() if not (isinstance(d, int) and d > 0)]
    if dead:
        for n, _ in dead:
            print(f"  timeout  {n}")


def cmd_conns():
    data = api("/connections")
    conns = data.get("connections", [])
    print(f"Active connections: {len(conns)}")
    print("─" * 70)
    for c in sorted(conns, key=lambda x: x.get("start", ""), reverse=True)[:30]:
        meta = c.get("metadata", {})
        host = meta.get("host", "") or meta.get("destinationIP", "")
        port = meta.get("destinationPort", "")
        network = meta.get("network", "")
        typ = meta.get("type", "")
        chains = " -> ".join(c.get("chains", []))
        dl = c.get("download", 0) / 1024
        ul = c.get("upload", 0) / 1024
        print(f"  {typ:5s} {network:3s} {host}:{port}")
        print(f"        chain: {chains}  ↓{dl:.1f}KB ↑{ul:.1f}KB")


def cmd_killall():
    api("/connections", "DELETE")
    print("All connections closed.")


def cmd_dns(domain, qtype="A"):
    data = api(f"/dns/query?name={domain}&type={qtype}")
    for a in data.get("Answer", []):
        print(f"  {a['name']:30s} TTL={a['TTL']:5d}  {a['data']}")
    if not data.get("Answer"):
        print("  (no answer)")


def cmd_flush_dns():
    api("/cache/dns/flush", "POST")
    print("DNS cache flushed.")


def cmd_flush_fakeip():
    api("/cache/fakeip/flush", "POST")
    print("FakeIP cache flushed.")


def cmd_reload(path=None):
    if path:
        api("/configs?force=true", "PUT", {"path": path, "payload": ""})
    else:
        reload_config()
    print("Config reloaded.")


def cmd_patch(json_str):
    api("/configs", "PATCH", json.loads(json_str))
    print("Config patched.")


def cmd_traffic():
    print("Streaming traffic... Ctrl+C to stop")
    conn, resp = api_stream("/traffic")
    try:
        while True:
            line = resp.readline().decode().strip()
            if not line:
                continue
            t = json.loads(line)
            up = t["up"] / 1024
            down = t["down"] / 1024
            up_t = t["upTotal"] / 1024 / 1024
            down_t = t["downTotal"] / 1024 / 1024
            print(
                f"  ↑ {up:8.1f} KB/s  ↓ {down:8.1f} KB/s  |  Total ↑ {up_t:.1f} MB  ↓ {down_t:.1f} MB",
                flush=True,
            )
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


def cmd_logs(level="info"):
    print(f"Streaming logs (level={level})... Ctrl+C to stop")
    conn, resp = api_stream(f"/logs?level={level}")
    try:
        while True:
            line = resp.readline().decode().strip()
            if not line:
                continue
            log = json.loads(line)
            print(f"[{log['type']:7s}] {log['payload']}")
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


def cmd_memory():
    print("Memory usage... Ctrl+C to stop")
    conn, resp = api_stream("/memory")
    try:
        while True:
            line = resp.readline().decode().strip()
            if not line:
                continue
            m = json.loads(line)
            inuse = m["inuse"] / 1024 / 1024
            print(f"  Memory in use: {inuse:.1f} MB", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


def cmd_rules():
    data = api("/rules")
    rules = data.get("rules", [])
    print(f"Total rules: {len(rules)}")
    for r in rules:
        print(f"  {r['type']:15s} {r['payload']:40s} => {r['proxy']}")


def cmd_providers():
    data = api("/providers/proxies")
    for name, info in sorted(data.get("providers", {}).items()):
        typ = info.get("type", "")
        cnt = len(info.get("proxies", []))
        print(f"  [{typ:10s}] {name:25s} ({cnt} proxies)")


def cmd_healthcheck(provider):
    api(f"/providers/proxies/{urlencode(provider)}/healthcheck")
    print(f"Health check triggered for '{provider}'.")


def cmd_restart():
    api("/restart", "POST", {"path": "", "payload": ""})
    print("Mihomo restarted.")


# ─── Subscription update ──────────────────────────────────────────────────────


def _fetch_subscriptions():
    """Fetch subscription files. Handles both own and Verge format."""
    os.makedirs(PROFILES_DIR, exist_ok=True)

    with open(PROFILES_YAML) as f:
        data = yaml.safe_load(f)

    if USE_VERGE:
        # Verge format: items list with type=remote
        for item in data.get("items", []):
            if item.get("type") != "remote":
                continue
            name = item.get("name", item.get("uid", "?"))
            url = item.get("url", "")
            if not url:
                continue
            print(f"  Fetching: {name} ...")
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "clash-verge/v2.2"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    content = resp.read()
                filepath = os.path.join(PROFILES_DIR, item["file"])
                with open(filepath, "wb") as f:
                    f.write(content)
                item["updated"] = int(time.time())
                print(f"    ✅ {name} ({len(content)} bytes)")
            except Exception as e:
                print(f"    ❌ {name}: {e}")
    else:
        # Own format: profiles dict
        for key, profile in data.get("profiles", {}).items():
            name = profile.get("name", key)
            url = profile.get("url", "")
            if not url:
                continue
            print(f"  Fetching: {name} ...")
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "mihomo-ctl/1.0"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    content = resp.read()
                filepath = os.path.join(PROFILES_DIR, profile["file"])
                with open(filepath, "wb") as f:
                    f.write(content)
                profile["updated"] = int(time.time())
                print(f"    ✅ {name} ({len(content)} bytes)")
            except Exception as e:
                print(f"    ❌ {name}: {e}")

    with open(PROFILES_YAML, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def cmd_sub():
    print("=== Updating subscriptions ===")
    _fetch_subscriptions()
    print("")
    cmd_apply()


# ─── Apply global script & generate config ────────────────────────────────────


def cmd_apply():
    print("=== Applying config ===")

    # Load profiles.yaml
    with open(PROFILES_YAML) as f:
        profiles_data = yaml.safe_load(f)

    if USE_VERGE:
        # Verge format
        current_uid = profiles_data.get("current", "")
        profile_item = None
        for item in profiles_data.get("items", []):
            if item.get("uid") == current_uid:
                profile_item = item
                break
        if not profile_item:
            print(f"Error: current profile uid '{current_uid}' not found")
            sys.exit(1)
        profile_file = profile_item["file"]
        profile_name = profile_item.get("name", "")
    else:
        # Own format
        current = profiles_data.get("current", "")
        profile = profiles_data.get("profiles", {}).get(current)
        if not profile:
            print(f"Error: current profile '{current}' not found in profiles.yaml")
            sys.exit(1)
        profile_file = profile["file"]
        profile_name = profile.get("name", "")

    # Load base config
    with open(CONFIG_YAML) as f:
        base_config = yaml.safe_load(f) or {}

    # Load subscription profile
    profile_path = os.path.join(PROFILES_DIR, profile_file)
    if not os.path.exists(profile_path):
        print(f"Error: profile file not found: {profile_path}")
        print("  Run 'mihomo-ctl sub' to fetch subscriptions first.")
        sys.exit(1)

    with open(profile_path) as f:
        sub_config = yaml.safe_load(f) or {}

    # Merge: base config + subscription data
    final = dict(base_config)
    for key in [
        "proxies",
        "proxy-groups",
        "rules",
        "rule-providers",
        "proxy-providers",
    ]:
        if key in sub_config:
            final[key] = sub_config[key]

    # Merge DNS (subscription can override/extend)
    if "dns" in sub_config:
        if "dns" in final:
            final["dns"].update(sub_config["dns"])
        else:
            final["dns"] = sub_config["dns"]

    # Run global script via node.js
    if os.path.exists(GLOBAL_SCRIPT):
        with open(GLOBAL_SCRIPT) as f:
            script_content = f.read()

        runner = f"""\
const fs = require('fs');
const config = JSON.parse(fs.readFileSync('/dev/stdin', 'utf8'));
{script_content}
const result = main(config, '{profile_name}');
process.stdout.write(JSON.stringify(result));
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False
        ) as tmp:
            tmp.write(runner)
            tmp_path = tmp.name

        try:
            proc = subprocess.run(
                ["node", tmp_path],
                input=json.dumps(final),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                final = json.loads(proc.stdout)
                print("  ✅ Global script applied")
            else:
                print(f"  ⚠️  Script error: {proc.stderr[:300]}")
        finally:
            os.unlink(tmp_path)
    else:
        print("  (no script.js found, skipping)")

    # Write generated config
    with open(GENERATED_YAML, "w") as f:
        yaml.dump(
            final, f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

    n_proxies = len(final.get("proxies", []))
    n_groups = len(final.get("proxy-groups", []))
    print(f"  ✅ Generated config ({n_proxies} proxies, {n_groups} groups)")
    print(f"     {GENERATED_YAML}")

    # Reload or start our own mihomo
    if not USE_VERGE and os.path.exists(SOCK_OWN):
        # Our own mihomo is running, reload it
        try:
            reload_config()
            print("  ✅ Mihomo reloaded.")
        except Exception:
            print("  ⚠️  Failed to reload, restarting...")
            _start_mihomo()
    elif not USE_VERGE:
        # Our own mihomo is not running, start it
        _start_mihomo()
    else:
        # --verge mode: reload via Verge's socket
        try:
            reload_config()
            print("  ✅ Mihomo reloaded.")
        except Exception:
            print(f"  ℹ️  Mihomo not running. Start Clash Verge or run:")
            print(f"     mihomo -f {GENERATED_YAML}")


# ─── Start / stop own mihomo ──────────────────────────────────────────────────


def _find_mihomo_bin():
    """Find mihomo binary: own kernel > PATH."""
    own = os.path.join(OWN_DIR, "bin", "mihomo")
    if os.path.exists(own) and os.access(own, os.X_OK):
        return own
    path_bin = shutil.which("mihomo")
    if path_bin:
        return path_bin
    return None


def _is_own_mihomo_running():
    """Check if our own mihomo is already running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "--", f"-ext-ctl-unix {SOCK_OWN}"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _start_mihomo():
    """Start our own mihomo in background."""
    if _is_own_mihomo_running():
        print("  ℹ️  Own mihomo already running.")
        return

    binary = _find_mihomo_bin()
    if not binary:
        print(f"  ❌ No mihomo binary found.")
        print(f"     Install with: mihomo-ctl kernel upgrade")
        return

    cmd = [
        binary,
        "-d", OWN_DIR,
        "-f", GENERATED_YAML,
        "-ext-ctl-unix", SOCK_OWN,
    ]

    # Start detached
    log_path = os.path.join(OWN_DIR, "mihomo.log")
    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )

    # Wait briefly to check it started
    time.sleep(1)
    if proc.poll() is not None:
        print(f"  ❌ Mihomo exited immediately (code {proc.returncode})")
        print(f"     Check logs: {log_path}")
        return

    print(f"  ✅ Mihomo started (pid={proc.pid})")
    print(f"     Binary: {binary}")
    print(f"     Socket: {SOCK_OWN}")
    print(f"     Log:    {log_path}")


def _stop_mihomo():
    """Stop our own mihomo processes (matched by -ext-ctl-unix SOCK_OWN)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "--", f"-ext-ctl-unix {SOCK_OWN}"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
    except Exception:
        pids = []

    if not pids:
        print("  ℹ️  Own mihomo not running.")
        return

    for pid in pids:
        try:
            os.kill(pid, 15)  # SIGTERM
            print(f"  ✅ Stopped mihomo (pid={pid})")
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"  ⚠️  Permission denied for pid={pid} (try sudo)")

    # Clean up stale socket
    time.sleep(0.5)
    if os.path.exists(SOCK_OWN):
        try:
            os.unlink(SOCK_OWN)
        except OSError:
            pass


# ─── Kernel version management ────────────────────────────────────────────────

KERNEL_DIR = os.path.join(OWN_DIR, "bin")
KERNEL_PATH = os.path.join(KERNEL_DIR, "mihomo")
GITHUB_API = "https://api.github.com/repos/MetaCubeX/mihomo/releases"
GITHUB_DL = "https://github.com/MetaCubeX/mihomo/releases/download"


def _detect_platform():
    """Detect OS and arch, return (os_name, arch_suffix) for release matching."""
    system = platform.system().lower()  # darwin, linux
    machine = platform.machine().lower()  # arm64, x86_64, aarch64

    if system == "darwin":
        os_name = "darwin"
    elif system == "linux":
        os_name = "linux"
    else:
        print(f"Error: unsupported OS '{system}'")
        sys.exit(1)

    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "amd64"
    else:
        print(f"Error: unsupported architecture '{machine}'")
        sys.exit(1)

    return os_name, arch


def _github_api(path):
    """Call GitHub API and return JSON."""
    url = GITHUB_API + path
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "mihomo-ctl/1.0",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _get_latest_release():
    """Get latest stable release tag and version."""
    data = _github_api("/latest")
    return data["tag_name"]


def _list_recent_releases(n=10):
    """List recent release tags."""
    data = _github_api(f"?per_page={n}")
    return [r["tag_name"] for r in data if not r.get("prerelease")]


def _build_asset_name(version, os_name, arch):
    """Build the expected asset filename.

    Naming convention: mihomo-{os}-{arch}-{version}.gz
    For darwin-arm64: mihomo-darwin-arm64-v1.19.22.gz
    For linux-amd64:  mihomo-linux-amd64-v1.19.22.gz  (default = GOAMD64=v3)
    """
    return f"mihomo-{os_name}-{arch}-{version}.gz"


def _get_current_kernel_version():
    """Get version of the currently running/installed kernel.

    Tries (in order):
    1. Query running mihomo via API socket
    2. Run the local binary with -v
    """
    # Try running instance first
    try:
        sock = _find_sock()
        if sock:
            conn = UnixHTTPConnection(sock)
            headers = {"Content-Type": "application/json"}
            conn.request("GET", "/version", headers=headers)
            resp = conn.getresponse()
            ver = json.loads(resp.read().decode())
            conn.close()
            if isinstance(ver, dict) and ver.get("version"):
                v = ver["version"]
                return v if v.startswith("v") else f"v{v}"
    except Exception:
        pass

    # Fallback: local binary
    if not os.path.exists(KERNEL_PATH):
        return None
    try:
        result = subprocess.run(
            [KERNEL_PATH, "-v"],
            capture_output=True, text=True, timeout=5,
        )
        # Output like: "Mihomo Meta v1.19.22 ..." or just version
        output = result.stdout.strip()
        for word in output.split():
            if word.startswith("v") and "." in word:
                return word
        return output or None
    except Exception:
        return None


def _download_and_install(version):
    """Download a specific version and install to KERNEL_DIR."""
    os_name, arch = _detect_platform()
    asset = _build_asset_name(version, os_name, arch)
    url = f"{GITHUB_DL}/{version}/{asset}"

    print(f"  Platform:  {os_name}-{arch}")
    print(f"  Version:   {version}")
    print(f"  Asset:     {asset}")
    print(f"  URL:       {url}")
    print()

    # Download
    print("  Downloading...", end="", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "mihomo-ctl/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            compressed = resp.read()
    except urllib.error.HTTPError as e:
        print(f" ❌")
        print(f"  Error: HTTP {e.code} - {e.reason}")
        if e.code == 404:
            print(f"  Asset not found. Check available versions with: mihomo-ctl kernel versions")
        sys.exit(1)
    print(f" ✅ ({len(compressed) / 1024 / 1024:.1f} MB)")

    # Decompress
    print("  Decompressing...", end="", flush=True)
    binary = gzip.decompress(compressed)
    print(f" ✅ ({len(binary) / 1024 / 1024:.1f} MB)")

    # Install
    os.makedirs(KERNEL_DIR, exist_ok=True)
    backup_path = KERNEL_PATH + ".bak"

    # Backup existing
    if os.path.exists(KERNEL_PATH):
        shutil.copy2(KERNEL_PATH, backup_path)
        print(f"  Backed up existing kernel to {backup_path}")

    # Write new binary
    with tempfile.NamedTemporaryFile(dir=KERNEL_DIR, delete=False) as tmp:
        tmp.write(binary)
        tmp_path = tmp.name

    os.chmod(tmp_path, 0o755)
    shutil.move(tmp_path, KERNEL_PATH)
    print(f"  ✅ Installed to {KERNEL_PATH}")

    # Verify
    new_ver = _get_current_kernel_version()
    if new_ver:
        print(f"  ✅ Verified: {new_ver}")


def cmd_kernel(action=None, *kernel_args):
    """Kernel version management."""
    if action is None or action == "status":
        ver = _get_current_kernel_version()
        if ver:
            print(f"  Installed: {ver}")
            print(f"  Path:      {KERNEL_PATH}")
        else:
            print(f"  No kernel found at {KERNEL_PATH}")
        try:
            latest = _get_latest_release()
            print(f"  Latest:    {latest}")
            if ver and ver != latest:
                print(f"  ⬆️  Upgrade available! Run: mihomo-ctl kernel upgrade")
        except Exception as e:
            print(f"  (could not check latest: {e})")

    elif action == "versions":
        flags = set(kernel_args)
        if "--upgrade" in flags or "-u" in flags:
            # Show versions and upgrade to latest
            cur = _get_current_kernel_version()
            latest = _get_latest_release()
            print(f"  Current:  {cur or 'not installed'}")
            print(f"  Latest:   {latest}")
            if cur == latest:
                print("  ✅ Already up to date.")
                return
            print()
            _download_and_install(latest)
        else:
            # List recent versions
            cur = _get_current_kernel_version()
            print(f"  Current:  {cur or 'not installed'}")
            print()
            print("=== Recent Releases ===")
            releases = _list_recent_releases()
            for tag in releases:
                marker = " ◀ installed" if tag == cur else ""
                print(f"  {tag}{marker}")
            print()
            print("  Upgrade to latest: mihomo-ctl kernel versions --upgrade")
            print("  Install specific:  mihomo-ctl kernel install <version>")

    elif action == "upgrade":
        cur = _get_current_kernel_version()
        latest = _get_latest_release()
        print(f"  Current:  {cur or 'not installed'}")
        print(f"  Latest:   {latest}")
        if cur == latest:
            print("  ✅ Already up to date.")
            return
        print()
        _download_and_install(latest)

    elif action == "install":
        if not kernel_args:
            print("Usage: mihomo-ctl kernel install <version>")
            print("  e.g. mihomo-ctl kernel install v1.19.22")
            sys.exit(1)
        version = kernel_args[0]
        if not version.startswith("v"):
            version = "v" + version
        _download_and_install(version)

    elif action == "path":
        print(KERNEL_PATH)

    else:
        print("Usage: mihomo-ctl kernel <command>")
        print("  status               Show installed & latest version")
        print("  versions             List recent releases")
        print("  versions --upgrade   List versions & upgrade to latest")
        print("  upgrade              Upgrade to latest release")
        print("  install <version>    Install a specific version")
        print("  path                 Print kernel binary path")
        sys.exit(1)


# ─── Script view/edit ─────────────────────────────────────────────────────────


def cmd_script(action=None):
    if action == "edit":
        editor = os.environ.get("EDITOR", "vim")
        os.execvp(editor, [editor, GLOBAL_SCRIPT])
    else:
        if os.path.exists(GLOBAL_SCRIPT):
            with open(GLOBAL_SCRIPT) as f:
                print(f.read())
        else:
            print(f"No script found at {GLOBAL_SCRIPT}")


# ─── Profile management ──────────────────────────────────────────────────────


def cmd_profile(action=None, *profile_args):
    with open(PROFILES_YAML) as f:
        data = yaml.safe_load(f)

    if action is None or action == "list":
        current = data.get("current", "")
        print("=== Profiles ===")
        for key, p in data.get("profiles", {}).items():
            marker = " ◀" if key == current else ""
            updated = p.get("updated", "never")
            if isinstance(updated, int):
                updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
            print(f"  {key:15s} {p.get('name',''):15s} updated: {updated}{marker}")
    elif action == "use":
        if not profile_args:
            print("Usage: mihomo-ctl profile use <name>")
            sys.exit(1)
        name = profile_args[0]
        if name not in data.get("profiles", {}):
            print(f"Error: profile '{name}' not found")
            sys.exit(1)
        data["current"] = name
        with open(PROFILES_YAML, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        print(f"Switched to profile: {name}")
        cmd_apply()
    elif action == "add":
        if len(profile_args) < 2:
            print("Usage: mihomo-ctl profile add <name> <url>")
            sys.exit(1)
        name, url = profile_args[0], profile_args[1]
        if "profiles" not in data:
            data["profiles"] = {}
        data["profiles"][name] = {
            "name": name,
            "url": url,
            "file": f"{name}.yaml",
        }
        with open(PROFILES_YAML, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        print(f"Added profile: {name}")
    else:
        print("Usage: mihomo-ctl profile [list|use <name>|add <name> <url>]")


# ─── Main ─────────────────────────────────────────────────────────────────────

HELP = """\
mihomo-ctl - standalone CLI controller for mihomo

FLAGS:
  --verge              Use Clash Verge's config directory instead of own

CONFIG DIR: ~/.local/share/mihomo/  (or Clash Verge dir with --verge)
  config.yaml     Base config (ports, dns, tun)
  profiles.yaml   Subscription list
  profiles/       Downloaded subscription files
  script.js       Global enhancement script (node.js)
  generated.yaml  Final config for mihomo

USAGE:
  mihomo-ctl <command> [arguments]

COMMANDS:
  Status & Info:
    status, s              Show mihomo status
    traffic, t             Stream real-time traffic
    memory, mem            Stream memory usage
    logs [level]           Stream logs (info/warning/error/debug)

  Proxy Control:
    groups, g              List all proxy groups
    list, l <group>        List proxies in a group (◀ = current)
    switch, sw <grp> <px>  Switch selector group
    delay, d <proxy>       Test proxy delay
    gdelay, gd <group>     Test group delay

  Mode:
    mode, m                Show current mode
    mode, m <mode>         Switch mode (rule|global|direct)

  Connections:
    conns, c               List active connections
    killall, ka            Close all connections

  DNS:
    dns <domain> [type]    Query DNS
    flush-dns              Flush DNS cache
    flush-fakeip           Flush FakeIP cache

  Config & Subscription:
    sub, update            Fetch subscriptions + apply + reload
    apply, a               Run script + generate config + reload
    reload, r              Force reload current config
    patch, p '<json>'      Hot-patch running config
    script, sc             View global script
    script edit            Edit script in $EDITOR
    profile                List profiles
    profile use <name>     Switch active profile
    profile add <n> <url>  Add a new subscription

  Providers:
    providers              List proxy providers
    healthcheck, hc <name> Trigger provider health check

  Rules:
    rules                  List all rules

  Process:
    start                  Start own mihomo instance
    stop                   Stop own mihomo instance

  Kernel:
    kernel, k              Show installed & latest kernel version
    kernel versions        List recent releases
    kernel versions -u     List + upgrade to latest
    kernel upgrade         Upgrade kernel to latest release
    kernel install <ver>   Install a specific version
    kernel path            Print kernel binary path

  System:
    restart                Restart mihomo kernel

EXAMPLES:
  mihomo-ctl sub                        # fetch subs + apply + reload
  mihomo-ctl --verge sub                # same but using Verge's config
  mihomo-ctl apply                      # re-run script + reload
  mihomo-ctl groups
  mihomo-ctl switch 'AI Suite' 'AI Auto'
  mihomo-ctl gdelay Fast
  mihomo-ctl profile add xyz 'https://...'
  mihomo-ctl profile use xyz
  mihomo-ctl script edit
  mihomo-ctl kernel versions              # list recent releases
  mihomo-ctl kernel versions --upgrade    # list + upgrade to latest
  mihomo-ctl kernel upgrade               # upgrade to latest
  mihomo-ctl kernel install v1.19.22      # install specific version
"""


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "help"

    match cmd:
        case "status" | "s":
            cmd_status()
        case "mode" | "m":
            cmd_mode(args[1] if len(args) > 1 else None)
        case "groups" | "g":
            cmd_groups()
        case "list" | "l":
            if len(args) < 2:
                print("Usage: mihomo-ctl list <group-name>")
                sys.exit(1)
            cmd_list(args[1])
        case "switch" | "sw":
            if len(args) < 3:
                print("Usage: mihomo-ctl switch <group> <proxy>")
                sys.exit(1)
            cmd_switch(args[1], args[2])
        case "delay" | "d":
            if len(args) < 2:
                print("Usage: mihomo-ctl delay <proxy> [url] [timeout]")
                sys.exit(1)
            cmd_delay(args[1], *args[2:])
        case "gdelay" | "gd":
            if len(args) < 2:
                print("Usage: mihomo-ctl gdelay <group> [url] [timeout]")
                sys.exit(1)
            cmd_gdelay(args[1], *args[2:])
        case "conns" | "c":
            cmd_conns()
        case "killall" | "ka":
            cmd_killall()
        case "dns":
            if len(args) < 2:
                print("Usage: mihomo-ctl dns <domain> [type]")
                sys.exit(1)
            cmd_dns(args[1], args[2] if len(args) > 2 else "A")
        case "flush-dns":
            cmd_flush_dns()
        case "flush-fakeip":
            cmd_flush_fakeip()
        case "reload" | "r":
            cmd_reload(args[1] if len(args) > 1 else None)
        case "patch" | "p":
            if len(args) < 2:
                print("Usage: mihomo-ctl patch '<json>'")
                sys.exit(1)
            cmd_patch(args[1])
        case "traffic" | "t":
            cmd_traffic()
        case "logs":
            cmd_logs(args[1] if len(args) > 1 else "info")
        case "memory" | "mem":
            cmd_memory()
        case "rules":
            cmd_rules()
        case "providers":
            cmd_providers()
        case "healthcheck" | "hc":
            if len(args) < 2:
                print("Usage: mihomo-ctl healthcheck <provider>")
                sys.exit(1)
            cmd_healthcheck(args[1])
        case "start":
            _start_mihomo()
        case "stop":
            _stop_mihomo()
        case "restart":
            cmd_restart()
        case "sub" | "update":
            cmd_sub()
        case "apply" | "a":
            cmd_apply()
        case "script" | "sc":
            cmd_script(args[1] if len(args) > 1 else None)
        case "profile" | "prof":
            cmd_profile(*args[1:])
        case "kernel" | "k":
            cmd_kernel(*args[1:])
        case "help" | "h" | "--help" | "-h":
            print(HELP)
        case _:
            print(f"Unknown command: {cmd}")
            print("Run 'mihomo-ctl help' for usage.")
            sys.exit(1)


if __name__ == "__main__":
    main()
