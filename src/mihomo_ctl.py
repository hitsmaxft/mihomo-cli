# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""mihomo-ctl: standalone CLI controller for mihomo.
Optimized for agent use: rich output, smart defaults, reduced calls.
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
    "~/.config/mihomo-ctl/verge"
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

# ─── Options ───────────────────────────────────────────────────────────────────

JSON_OUTPUT = "--json" in sys.argv
HINTS = "--hints" in sys.argv or "--help" in sys.argv
if JSON_OUTPUT:
    sys.argv.remove("--json")
if "--hints" in sys.argv:
    sys.argv.remove("--hints")
if "--no-hints" in sys.argv:
    sys.argv.remove("--no-hints")
    HINTS = False

# ─── Unix socket HTTP client ──────────────────────────────────────────────────


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
    result = {"error": "no mihomo socket found", "hint": "Is mihomo running?"}
    if JSON_OUTPUT:
        print(json.dumps(result))
    else:
        print("Error: no mihomo socket found. Is mihomo running?")
    sys.exit(1)


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


def _out(data, **extra):
    """Output handler: JSON or pretty print."""
    if JSON_OUTPUT:
        if isinstance(data, str):
            print(json.dumps({"output": data, **extra}))
        else:
            print(json.dumps({**data, **extra}))
    else:
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"  {k}: {v}")
        else:
            print(data)


# ─── Combined commands for agent efficiency ────────────────────────────────────

def cmd_overview():
    """Get everything an agent needs in one call.
    
    Returns: groups with current node, total proxies, quick status.
    """
    ver = api("/version")
    cfg = api("/configs")
    groups_data = api("/group")
    
    result = {
        "version": ver.get("version", "?"),
        "mode": cfg.get("mode", "?"),
        "mixed_port": cfg.get("mixed-port", "?"),
        "tun": cfg.get("tun", {}).get("enable", False),
        "socket": get_sock(),
        "groups": [],
    }
    
    for g in groups_data.get("proxies", []):
        group_info = {
            "name": g["name"],
            "type": g["type"],
            "current": g.get("now", "N/A"),
            "proxies": g.get("all", []),
            "count": len(g.get("all", [])),
        }
        result["groups"].append(group_info)
    
    if JSON_OUTPUT:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== Mihomo v{result['version']} ({result['mode']} mode) ===")
        print(f"Socket: {result['socket']}")
        print(f"Port: {result['mixed_port']} | TUN: {result['tun']}")
        print()
        print("Groups:")
        for g in result["groups"]:
            marker = ""
            if "Auto" in g["name"] or "Fast" in g["name"]:
                marker = " ⭐"
            print(f"  {g['name']:20s} ({g['count']:2d} nodes) => {g['current']}{marker}")
        
        if HINTS:
            print()
            print("Hints:")
            print("  mihomo-ctl gdelay <group>   # Test group latency")
            print("  mihomo-ctl switch <grp> <node>  # Switch node")
            print("  mihomo-ctl sub            # Update subscriptions")


def cmd_gdelay_with_switch(group_name=None, auto=False):
    """Test group latency and suggest/apply best node.
    
    Args:
        group_name: Group to test (auto-detect if None)
        auto: If True, auto-switch to fastest node
    """
    groups_data = api("/group")
    groups = groups_data.get("proxies", [])
    
    # Auto-detect best group to test
    if not group_name:
        # Prefer groups with "Fast" or "Auto" in name
        for g in groups:
            if "Fast" in g["name"] or "Auto" in g["name"]:
                group_name = g["name"]
                break
        if not group_name:
            group_name = groups[0]["name"] if groups else None
    
    if not group_name:
        result = {"error": "No groups found"}
        if JSON_OUTPUT:
            print(json.dumps(result))
        else:
            print("Error: No groups found")
        return
    
    result = api(f"/group/{urlencode(group_name)}/delay?url=https://www.gstatic.com/generate_204&timeout=5000")
    
    if not isinstance(result, dict):
        _out({"error": str(result)})
        return
    
    # Parse results
    alive = [(n, d) for n, d in result.items() if isinstance(d, int) and d > 0]
    dead = [(n, d) for n, d in result.items() if not (isinstance(d, int) and d > 0)]
    alive.sort(key=lambda x: x[1])
    
    output = {
        "group": group_name,
        "tested": len(alive) + len(dead),
        "alive": len(alive),
        "timeout": len(dead),
        "results": [{"node": n, "delay": d} for n, d in alive] + [{"node": n, "delay": -1} for n, _ in dead],
        "fastest": {"node": alive[0][0], "delay": alive[0][1]} if alive else None,
    }
    
    if JSON_OUTPUT:
        print(json.dumps(output, indent=2))
    else:
        print(f"=== Latency Test: {group_name} ===")
        print(f"Tested: {output['tested']} | Alive: {output['alive']} | Timeout: {output['timeout']}")
        print()
        if alive:
            print("By delay (ascending):")
            for name, delay in alive[:10]:
                marker = " ◀ CURRENT" if name == _get_current_for_group(group_name, groups) else ""
                print(f"  {delay:5d}ms  {name}{marker}")
        
        if dead:
            print("\nTimeout:")
            for name, _ in dead[:5]:
                print(f"  timeout  {name}")
        
        if output["fastest"]:
            print(f"\nFastest: {output['fastest']['node']} ({output['fastest']['delay']}ms)")
        
        if HINTS:
            print()
            print("Hints:")
            print(f"  mihomo-ctl switch {group_name} {output['fastest']['node']}  # Switch to fastest")
            if not auto and len(alive) > 1:
                print(f"  mihomo-ctl gdelay {group_name} --auto  # Auto-switch to fastest")
            
            # Suggest other actions
            print()
            print("Other groups:")
            for g in groups:
                if g["name"] != group_name and ("Fast" in g["name"] or "Auto" in g["name"]):
                    print(f"  mihomo-ctl gdelay {g['name']}")


def _get_current_for_group(group_name, groups):
    for g in groups:
        if g["name"] == group_name:
            return g.get("now", "")
    return ""


def cmd_switch_with_verification(group, proxy):
    """Switch node with verification and next-step hints."""
    groups_data = api("/group")
    groups = groups_data.get("proxies", [])
    
    # Get current before switch
    before = _get_current_for_group(group, groups)
    
    # Do switch
    result = api(f"/proxies/{urlencode(group)}", "PUT", {"name": proxy})
    
    # Verify
    after_groups = api("/group").get("proxies", [])
    after = _get_current_for_group(group, after_groups)
    
    success = after == proxy
    
    output = {
        "group": group,
        "target": proxy,
        "before": before,
        "after": after,
        "success": success,
    }
    
    if JSON_OUTPUT:
        print(json.dumps(output, indent=2))
    else:
        if success:
            print(f"✓ Switched [{group}]: {before} → {proxy}")
        else:
            print(f"✗ Failed to switch. Current: {after}")
        
        if HINTS:
            print()
            print("Hints:")
            print("  mihomo-ctl delay " + proxy + "  # Verify node works")
            print("  mihomo-ctl conns           # Check connections")
            print("  mihomo-ctl gdelay " + group + "  # Re-test group latency")


def cmd_sub_with_summary():
    """Update subscriptions and show summary."""
    print("=== Updating subscriptions ===")
    _fetch_subscriptions()
    print()
    cmd_apply()
    print()
    
    # Show new group state
    groups_data = api("/group")
    groups = groups_data.get("proxies", [])
    
    if JSON_OUTPUT:
        print(json.dumps({"groups": [{"name": g["name"], "current": g.get("now", ""), "count": len(g.get("all", []))} for g in groups]}))
    else:
        print("=== Updated Groups ===")
        for g in groups:
            marker = " ⭐" if "Fast" in g["name"] or "Auto" in g["name"] else ""
            print(f"  {g['name']:20s} ({len(g.get('all', []))} nodes) => {g.get('now', 'N/A')}{marker}")
        
        if HINTS:
            print()
            print("Hints:")
            print("  mihomo-ctl gdelay Fast    # Test the Fast group")
            print("  mihomo-ctl overview      # See all groups")


def cmd_status_with_groups():
    """Status + proxy groups overview."""
    ver = api("/version")
    cfg = api("/configs")
    groups_data = api("/group")
    groups = groups_data.get("proxies", [])
    
    # Traffic summary
    traffic = api("/traffic")
    
    result = {
        "version": ver.get("version", "?"),
        "mode": cfg.get("mode", "?"),
        "config": {
            "mixed_port": cfg.get("mixed-port"),
            "allow_lan": cfg.get("allow-lan"),
            "log_level": cfg.get("log-level"),
            "tun": cfg.get("tun", {}).get("enable"),
            "ipv6": cfg.get("ipv6"),
        },
        "socket": get_sock(),
        "traffic": {
            "up_speed": traffic.get("up", 0),
            "down_speed": traffic.get("down", 0),
            "up_total": traffic.get("upTotal", 0),
            "down_total": traffic.get("downTotal", 0),
        },
        "groups": [{"name": g["name"], "type": g["type"], "current": g.get("now", ""), "count": len(g.get("all", []))} for g in groups],
    }
    
    if JSON_OUTPUT:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== Mihomo v{result['version']} ===")
        print(f"Mode: {result['mode']} | Port: {result['config']['mixed_port']} | TUN: {result['config']['tun']}")
        
        t = result["traffic"]
        up = t["up_speed"] / 1024
        down = t["down_speed"] / 1024
        up_mb = t["up_total"] / 1024 / 1024
        down_mb = t["down_total"] / 1024 / 1024
        print(f"Traffic: ↑{up:.1f}KB/s ↓{down:.1f}KB/s | Total: ↑{up_mb:.1f}MB ↓{down_mb:.1f}MB")
        print()
        print("Groups:")
        for g in result["groups"]:
            marker = " ⭐" if "Fast" in g["name"] or "Auto" in g["name"] else ""
            print(f"  {g['name']:20s} ({g['count']:2d}) => {g['current']}{marker}")
        
        if HINTS:
            print()
            print("Hints:")
            print("  mihomo-ctl gdelay Fast       # Test Fast group latency")
            print("  mihomo-ctl switch <grp> <px> # Switch node")
            print("  mihomo-ctl sub              # Update subscriptions")


# ─── Original commands (simplified) ────────────────────────────────────────────

def cmd_status():
    ver = api("/version")
    cfg = api("/configs")
    _out({
        "version": ver.get("version", "?"),
        "mode": cfg.get("mode", "?"),
        "mixed_port": cfg.get("mixed-port"),
        "allow_lan": cfg.get("allow-lan"),
        "log_level": cfg.get("log-level"),
        "tun": cfg.get("tun", {}).get("enable"),
        "ipv6": cfg.get("ipv6"),
        "socket": get_sock(),
    })


def cmd_groups():
    data = api("/group")
    groups = data.get("proxies", [])
    
    result = [{"name": g["name"], "type": g["type"], "current": g.get("now", ""), "count": len(g.get("all", []))} for g in groups]
    
    if JSON_OUTPUT:
        print(json.dumps(result, indent=2))
    else:
        print("Groups:")
        for g in groups:
            t = g["type"]
            name = g["name"]
            now = g.get("now", "N/A")
            count = len(g.get("all", []))
            marker = " ⭐" if "Fast" in name or "Auto" in name else ""
            print(f"  [{t:10s}] {name:20s} ({count:2d}) => {now}{marker}")


def cmd_list(group):
    data = api(f"/proxies/{urlencode(group)}")
    if isinstance(data, str) or "message" in (data or {}):
        _out({"error": f"Group '{group}' not found"})
        sys.exit(1)
    now = data.get("now", "")
    all_proxies = data.get("all", [])
    
    result = {"group": group, "current": now, "proxies": [{"name": n, "selected": n == now} for n in all_proxies]}
    
    if JSON_OUTPUT:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== {group} ===")
        for i, n in enumerate(all_proxies):
            marker = " ◀" if n == now else ""
            print(f"  {i+1:2d}. {n}{marker}")


def cmd_switch(group, proxy):
    api(f"/proxies/{urlencode(group)}", "PUT", {"name": proxy})
    _out({"message": f"Switched [{group}] => {proxy}"})


def cmd_delay(proxy, url="https://www.gstatic.com/generate_204", timeout="5000"):
    result = api(f"/proxies/{urlencode(proxy)}/delay?url={url}&timeout={timeout}")
    if isinstance(result, dict) and "delay" in result:
        _out({"proxy": proxy, "delay": result["delay"], "unit": "ms"})
    else:
        _out(result)


def cmd_gdelay(group, url="https://www.gstatic.com/generate_204", timeout="5000"):
    result = api(f"/group/{urlencode(group)}/delay?url={url}&timeout={timeout}")
    if not isinstance(result, dict):
        _out({"error": str(result)})
        return
    
    alive = [(n, d) for n, d in result.items() if isinstance(d, int) and d > 0]
    alive.sort(key=lambda x: x[1])
    dead = [(n, d) for n, d in result.items() if not (isinstance(d, int) and d > 0)]
    
    output = {
        "group": group,
        "results": [{"node": n, "delay": d} for n, d in alive] + [{"node": n, "timeout": True} for n, _ in dead],
        "fastest": {"node": alive[0][0], "delay": alive[0][1]} if alive else None,
    }
    
    if JSON_OUTPUT:
        print(json.dumps(output, indent=2))
    else:
        print(f"=== {group} latency ===")
        for name, delay in alive:
            print(f"  {delay:5d}ms  {name}")
        for n, _ in dead:
            print(f"  timeout  {n}")


def cmd_conns():
    data = api("/connections")
    conns = data.get("connections", [])
    
    result = {
        "count": len(conns),
        "connections": [{
            "host": c.get("metadata", {}).get("host", ""),
            "port": c.get("metadata", {}).get("destinationPort", ""),
            "network": c.get("metadata", {}).get("network", ""),
            "chain": c.get("chains", []),
            "download": c.get("download", 0),
            "upload": c.get("upload", 0),
        } for c in conns[:30]]
    }
    
    if JSON_OUTPUT:
        print(json.dumps(result, indent=2))
    else:
        print(f"Active connections: {len(conns)}")
        print("-" * 70)
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
    _out({"message": "All connections closed"})


def cmd_dns(domain, qtype="A"):
    data = api(f"/dns/query?name={domain}&type={qtype}")
    answers = data.get("Answer", [])
    
    result = {"domain": domain, "type": qtype, "answers": [{"name": a["name"], "ttl": a["TTL"], "data": a["data"]} for a in answers]}
    
    if JSON_OUTPUT:
        print(json.dumps(result, indent=2))
    else:
        for a in answers:
            print(f"  {a['name']:30s} TTL={a['TTL']:5d}  {a['data']}")
        if not answers:
            print("  (no answer)")


def cmd_flush_dns():
    api("/cache/dns/flush", "POST")
    _out({"message": "DNS cache flushed"})


def cmd_flush_fakeip():
    api("/cache/fakeip/flush", "POST")
    _out({"message": "FakeIP cache flushed"})


def cmd_reload(path=None):
    if path:
        api("/configs?force=true", "PUT", {"path": path, "payload": ""})
    else:
        reload_config()
    _out({"message": "Config reloaded"})


def cmd_patch(json_str):
    api("/configs", "PATCH", json.loads(json_str))
    _out({"message": "Config patched"})


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
            print(f"  ↑ {up:8.1f} KB/s  ↓ {down:8.1f} KB/s  |  Total ↑ {up_t:.1f} MB  ↓ {down_t:.1f} MB", flush=True)
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
    _out({"total": len(rules), "rules": rules})


def cmd_providers():
    data = api("/providers/proxies")
    providers = [{"name": name, "type": info.get("type", ""), "count": len(info.get("proxies", []))} for name, info in sorted(data.get("providers", {}).items())]
    
    if JSON_OUTPUT:
        print(json.dumps(providers, indent=2))
    else:
        for p in providers:
            print(f"  [{p['type']:10s}] {p['name']:25s} ({p['count']} proxies)")


def cmd_healthcheck(provider):
    api(f"/providers/proxies/{urlencode(provider)}/healthcheck")
    _out({"message": f"Health check triggered for '{provider}'"})


def cmd_restart():
    api("/restart", "POST", {"path": "", "payload": ""})
    _out({"message": "Mihomo restarted"})


# ─── Subscription update ──────────────────────────────────────────────────────


def _fetch_subscriptions():
    """Fetch subscription files. Handles both own and Verge format."""
    os.makedirs(PROFILES_DIR, exist_ok=True)

    with open(PROFILES_YAML) as f:
        data = yaml.safe_load(f)

    if USE_VERGE:
        for item in data.get("items", []):
            if item.get("type") != "remote":
                continue
            name = item.get("name", item.get("uid", "?"))
            url = item.get("url", "")
            if not url:
                continue
            print(f"  Fetching: {name} ...")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "clash-verge/v2.2"})
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
        for key, profile in data.get("profiles", {}).items():
            name = profile.get("name", key)
            url = profile.get("url", "")
            if not url:
                continue
            print(f"  Fetching: {name} ...")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "mihomo-ctl/1.0"})
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


def cmd_apply():
    print("=== Applying config ===")

    with open(PROFILES_YAML) as f:
        profiles_data = yaml.safe_load(f)

    if USE_VERGE:
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
        current = profiles_data.get("current", "")
        profile = profiles_data.get("profiles", {}).get(current)
        if not profile:
            print(f"Error: current profile '{current}' not found in profiles.yaml")
            sys.exit(1)
        profile_file = profile["file"]
        profile_name = profile.get("name", "")

    with open(CONFIG_YAML) as f:
        base_config = yaml.safe_load(f) or {}

    profile_path = os.path.join(PROFILES_DIR, profile_file)
    if not os.path.exists(profile_path):
        print(f"Error: profile file not found: {profile_path}")
        print("  Run 'mihomo-ctl sub' to fetch subscriptions first.")
        sys.exit(1)

    with open(profile_path) as f:
        sub_config = yaml.safe_load(f) or {}

    final = dict(base_config)
    for key in ["proxies", "proxy-groups", "rules", "rule-providers", "proxy-providers"]:
        if key in sub_config:
            final[key] = sub_config[key]

    if "dns" in sub_config:
        if "dns" in final:
            final["dns"].update(sub_config["dns"])
        else:
            final["dns"] = sub_config["dns"]

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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp:
            tmp.write(runner)
            tmp_path = tmp.name

        try:
            proc = subprocess.run(["node", tmp_path], input=json.dumps(final), capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                final = json.loads(proc.stdout)
                print("  ✅ Global script applied")
            else:
                print(f"  ⚠️  Script error: {proc.stderr[:300]}")
        finally:
            os.unlink(tmp_path)
    else:
        print("  (no script.js found, skipping)")

    with open(GENERATED_YAML, "w") as f:
        yaml.dump(final, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    n_proxies = len(final.get("proxies", []))
    n_groups = len(final.get("proxy-groups", []))
    print(f"  ✅ Generated config ({n_proxies} proxies, {n_groups} groups)")
    print(f"     {GENERATED_YAML}")

    if not USE_VERGE and os.path.exists(SOCK_OWN):
        try:
            reload_config()
            print("  ✅ Mihomo reloaded.")
        except Exception:
            print("  ⚠️  Failed to reload, restarting...")
            _start_mihomo()
    elif not USE_VERGE:
        _start_mihomo()
    else:
        try:
            reload_config()
            print("  ✅ Mihomo reloaded.")
        except Exception:
            print(f"  ℹ️  Mihomo not running. Start Clash Verge or run:")
            print(f"     mihomo -f {GENERATED_YAML}")


# ─── Kernel version management ────────────────────────────────────────────────

KERNEL_DIR = os.path.join(OWN_DIR, "bin")
KERNEL_PATH = os.path.join(KERNEL_DIR, "mihomo")
GITHUB_API = "https://api.github.com/repos/MetaCubeX/mihomo/releases"
GITHUB_DL = "https://github.com/MetaCubeX/mihomo/releases/download"


def _detect_platform():
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_name = "darwin"
    elif system == "linux":
        os_name = "linux"
    else:
        _out({"error": f"unsupported OS '{system}'"})
        sys.exit(1)

    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "amd64"
    else:
        _out({"error": f"unsupported architecture '{machine}'"})
        sys.exit(1)

    return os_name, arch


def _github_api(path):
    url = GITHUB_API + path
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "mihomo-ctl/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _get_latest_release():
    data = _github_api("/latest")
    return data["tag_name"]


def _list_recent_releases(n=10):
    data = _github_api(f"?per_page={n}")
    return [r["tag_name"] for r in data if not r.get("prerelease")]


def _build_asset_name(version, os_name, arch):
    return f"mihomo-{os_name}-{arch}-{version}.gz"


def _get_current_kernel_version():
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

    if not os.path.exists(KERNEL_PATH):
        return None
    try:
        result = subprocess.run([KERNEL_PATH, "-v"], capture_output=True, text=True, timeout=5)
        output = result.stdout.strip()
        for word in output.split():
            if word.startswith("v") and "." in word:
                return word
        return output or None
    except Exception:
        return None


def _download_and_install(version):
    os_name, arch = _detect_platform()
    asset = _build_asset_name(version, os_name, arch)
    url = f"{GITHUB_DL}/{version}/{asset}"

    print(f"  Platform:  {os_name}-{arch}")
    print(f"  Version:   {version}")
    print(f"  Asset:     {asset}")
    print(f"  URL:       {url}")
    print()

    print("  Downloading...", end="", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "mihomo-ctl/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            compressed = resp.read()
    except urllib.error.HTTPError as e:
        print(f" ❌")
        print(f"  Error: HTTP {e.code} - {e.reason}")
        sys.exit(1)
    print(f" ✅ ({len(compressed) / 1024 / 1024:.1f} MB)")

    print("  Decompressing...", end="", flush=True)
    binary = gzip.decompress(compressed)
    print(f" ✅ ({len(binary) / 1024 / 1024:.1f} MB)")

    os.makedirs(KERNEL_DIR, exist_ok=True)
    backup_path = KERNEL_PATH + ".bak"

    if os.path.exists(KERNEL_PATH):
        shutil.copy2(KERNEL_PATH, backup_path)
        print(f"  Backed up existing kernel to {backup_path}")

    with tempfile.NamedTemporaryFile(dir=KERNEL_DIR, delete=False) as tmp:
        tmp.write(binary)
        tmp_path = tmp.name

    os.chmod(tmp_path, 0o755)
    shutil.move(tmp_path, KERNEL_PATH)
    print(f"  ✅ Installed to {KERNEL_PATH}")

    new_ver = _get_current_kernel_version()
    if new_ver:
        print(f"  ✅ Verified: {new_ver}")


def cmd_kernel(action=None, *kernel_args):
    if action is None or action == "status":
        ver = _get_current_kernel_version()
        result = {"installed": ver, "path": KERNEL_PATH if ver else None}
        try:
            latest = _get_latest_release()
            result["latest"] = latest
            result["upgrade_available"] = ver != latest if ver else True
        except Exception as e:
            result["error"] = str(e)
        
        if JSON_OUTPUT:
            print(json.dumps(result, indent=2))
        else:
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
                elif ver:
                    print(f"  ✅ Up to date")
            except Exception as e:
                print(f"  (could not check latest: {e})")

    elif action == "versions":
        cur = _get_current_kernel_version()
        try:
            latest = _get_latest_release()
            releases = _list_recent_releases()
            
            if JSON_OUTPUT:
                print(json.dumps({"current": cur, "latest": latest, "releases": releases}, indent=2))
            else:
                print(f"  Current:  {cur or 'not installed'}")
                print(f"  Latest:   {latest}")
                print()
                print("Recent Releases:")
                for tag in releases:
                    marker = " ◀ installed" if tag == cur else (" ⭐ latest" if tag == latest else "")
                    print(f"  {tag}{marker}")
        except Exception as e:
            _out({"error": str(e)})

    elif action == "upgrade":
        cur = _get_current_kernel_version()
        latest = _get_latest_release()
        
        if cur == latest:
            _out({"message": "Already up to date", "version": latest})
            return
        
        _download_and_install(latest)

    elif action == "install":
        if not kernel_args:
            _out({"error": "Usage: mihomo-ctl kernel install <version>"})
            sys.exit(1)
        version = kernel_args[0]
        if not version.startswith("v"):
            version = "v" + version
        _download_and_install(version)

    elif action == "path":
        print(KERNEL_PATH)

    else:
        _out({"error": "Unknown kernel command. Use: status, versions, upgrade, install <version>, path"})


# ─── Profile management ──────────────────────────────────────────────────────


def cmd_profile(action=None, *profile_args):
    with open(PROFILES_YAML) as f:
        data = yaml.safe_load(f)

    if action is None or action == "list":
        current = data.get("current", "")
        
        if USE_VERGE:
            items = data.get("items", [])
            result = [{"uid": item.get("uid"), "name": item.get("name"), "type": item.get("type"), "selected": item.get("uid") == current} for item in items]
        else:
            result = [{"key": key, **p, "selected": key == current} for key, p in data.get("profiles", {}).items()]
        
        if JSON_OUTPUT:
            print(json.dumps(result, indent=2))
        else:
            print("=== Profiles ===")
            if USE_VERGE:
                for item in items:
                    marker = " ◀" if item.get("uid") == current else ""
                    print(f"  {item.get('name', item.get('uid', '?'))} [{item.get('type')}] {marker}")
            else:
                for key, p in data.get("profiles", {}).items():
                    marker = " ◀" if key == current else ""
                    updated = p.get("updated", "never")
                    if isinstance(updated, int):
                        updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
                    print(f"  {key:15s} {p.get('name',''):15s} updated: {updated}{marker}")
    
    elif action == "use":
        if not profile_args:
            _out({"error": "Usage: mihomo-ctl profile use <name>"})
            sys.exit(1)
        name = profile_args[0]
        
        if USE_VERGE:
            uid_exists = any(item.get("uid") == name for item in data.get("items", []))
            if not uid_exists:
                _out({"error": f"Profile '{name}' not found"})
                sys.exit(1)
            data["current"] = name
        else:
            if name not in data.get("profiles", {}):
                _out({"error": f"Profile '{name}' not found"})
                sys.exit(1)
            data["current"] = name
        
        with open(PROFILES_YAML, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        
        _out({"message": f"Switched to profile: {name}"})
        cmd_apply()
    
    elif action == "add":
        if len(profile_args) < 2:
            _out({"error": "Usage: mihomo-ctl profile add <name> <url>"})
            sys.exit(1)
        name, url = profile_args[0], profile_args[1]
        if "profiles" not in data:
            data["profiles"] = {}
        data["profiles"][name] = {"name": name, "url": url, "file": f"{name}.yaml"}
        with open(PROFILES_YAML, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        _out({"message": f"Added profile: {name}"})


# ─── Main ─────────────────────────────────────────────────────────────────────

HELP = """\
mihomo-ctl - CLI controller for mihomo (agent-optimized)

FLAGS:
  --verge    Use Clash Verge's config directory
  --json     JSON output for machine parsing
  --hints    Show action hints in output

COMMANDS (combined, agent-optimized):
  overview             Everything in one call: status + groups + suggestions
  gdelay <group>       Test latency + show fastest + hints
  switch <grp> <px>    Switch + verify + hints
  sub                  Update + apply + new group summary
  status               Status with groups overview

BASIC COMMANDS:
  groups, g            List all proxy groups
  list, l <group>      List proxies in a group (◀ = current)
  delay, d <proxy>     Test single proxy latency
  mode, m [mode]       Show/set mode (rule|global|direct)
  conns, c             List active connections
  killall, ka          Close all connections
  dns <domain> [type]  Query DNS
  flush-dns            Flush DNS cache
  reload, r            Reload config
  profile              List/manage profiles
  kernel, k            Kernel version management
  start/stop           Control mihomo process

EXAMPLES:
  mihomo-ctl overview                # Get all info at once
  mihomo-ctl gdelay Fast            # Test Fast group + show fastest
  mihomo-ctl gdelay --auto          # Auto-switch to fastest
  mihomo-ctl switch Fast 香港-01    # Switch with verification
  mihomo-ctl sub --json | jq .      # Parse subscription data
"""


def main():
    args = sys.argv[1:]
    
    # Check for global flags
    global HINTS, JSON_OUTPUT
    
    # Parse while preserving flags for commands that need them
    clean_args = []
    for i, arg in enumerate(args):
        if arg in ["--json", "--hints", "--no-hints"]:
            if arg == "--json":
                JSON_OUTPUT = True
            elif arg == "--hints":
                HINTS = True
            elif arg == "--no-hints":
                HINTS = False
        else:
            clean_args.append(arg)
    
    cmd = clean_args[0] if clean_args else "help"

    match cmd:
        case "overview":
            cmd_overview()
        case "gdelay" | "gd":
            group = clean_args[1] if len(clean_args) > 1 else None
            auto = "--auto" in clean_args
            if auto:
                clean_args = [c for c in clean_args if c != "--auto"]
                group = clean_args[1] if len(clean_args) > 1 else None
            cmd_gdelay_with_switch(group, auto)
        case "switch" | "sw":
            if len(clean_args) < 3:
                _out({"error": "Usage: mihomo-ctl switch <group> <proxy>"})
                sys.exit(1)
            cmd_switch_with_verification(clean_args[1], clean_args[2])
        case "sub" | "update":
            cmd_sub_with_summary()
        case "status" | "s":
            cmd_status_with_groups()
        case "mode" | "m":
            new_mode = clean_args[1] if len(clean_args) > 1 else None
            if new_mode:
                api("/configs", "PATCH", {"mode": new_mode})
                _out({"message": f"Mode switched to: {new_mode}"})
            else:
                cfg = api("/configs")
                _out({"mode": cfg.get("mode")})
        case "groups" | "g":
            cmd_groups()
        case "list" | "l":
            if len(clean_args) < 2:
                _out({"error": "Usage: mihomo-ctl list <group-name>"})
                sys.exit(1)
            cmd_list(clean_args[1])
        case "delay" | "d":
            if len(clean_args) < 2:
                _out({"error": "Usage: mihomo-ctl delay <proxy> [url] [timeout]"})
                sys.exit(1)
            cmd_delay(clean_args[1], *clean_args[2:])
        case "conns" | "c":
            cmd_conns()
        case "killall" | "ka":
            cmd_killall()
        case "dns":
            if len(clean_args) < 2:
                _out({"error": "Usage: mihomo-ctl dns <domain> [type]"})
                sys.exit(1)
            cmd_dns(clean_args[1], clean_args[2] if len(clean_args) > 2 else "A")
        case "flush-dns":
            cmd_flush_dns()
        case "flush-fakeip":
            cmd_flush_fakeip()
        case "reload" | "r":
            cmd_reload(clean_args[1] if len(clean_args) > 1 else None)
        case "patch" | "p":
            if len(clean_args) < 2:
                _out({"error": "Usage: mihomo-ctl patch '<json>'"})
                sys.exit(1)
            cmd_patch(clean_args[1])
        case "traffic" | "t":
            cmd_traffic()
        case "logs":
            cmd_logs(clean_args[1] if len(clean_args) > 1 else "info")
        case "memory" | "mem":
            cmd_memory()
        case "rules":
            cmd_rules()
        case "providers":
            cmd_providers()
        case "healthcheck" | "hc":
            if len(clean_args) < 2:
                _out({"error": "Usage: mihomo-ctl healthcheck <provider>"})
                sys.exit(1)
            cmd_healthcheck(clean_args[1])
        case "start":
            _start_mihomo()
        case "stop":
            _stop_mihomo()
        case "restart":
            cmd_restart()
        case "apply" | "a":
            cmd_apply()
        case "profile" | "prof":
            cmd_profile(*clean_args[1:])
        case "kernel" | "k":
            cmd_kernel(*clean_args[1:])
        case "help" | "h" | "--help" | "-h":
            print(HELP)
        case _:
            print(f"Unknown command: {cmd}")
            print("Run 'mihomo-ctl help' for usage.")
            sys.exit(1)


# ─── Process control (keep for compatibility) ──────────────────────────────────

SOCK_OWN = "/tmp/mihomo-ctl.sock"
OWN_DIR = os.path.expanduser("~/.local/share/mihomo")


def _find_mihomo_bin():
    own = os.path.join(OWN_DIR, "bin", "mihomo")
    if os.path.exists(own) and os.access(own, os.X_OK):
        return own
    path_bin = shutil.which("mihomo")
    if path_bin:
        return path_bin
    return None


def _is_own_mihomo_running():
    try:
        result = subprocess.run(["pgrep", "-f", "--", f"-ext-ctl-unix {SOCK_OWN}"], capture_output=True, text=True, timeout=5)
        return bool(result.stdout.strip())
    except Exception:
        return False


def _start_mihomo():
    if _is_own_mihomo_running():
        print("  ℹ️  Own mihomo already running.")
        return

    binary = _find_mihomo_bin()
    if not binary:
        print("  ❌ No mihomo binary found.")
        print("     Install with: mihomo-ctl kernel upgrade")
        return

    GENERATED_YAML = os.path.join(OWN_DIR, "generated.yaml")
    cmd = [binary, "-d", OWN_DIR, "-f", GENERATED_YAML, "-ext-ctl-unix", SOCK_OWN]

    log_path = os.path.join(OWN_DIR, "mihomo.log")
    log_file = open(log_path, "a")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=log_file, start_new_session=True)

    time.sleep(1)
    if proc.poll() is not None:
        print(f"  ❌ Mihomo exited immediately (code {proc.returncode})")
        print(f"     Check logs: {log_path}")
        return

    print(f"  ✅ Mihomo started (pid={proc.pid})")
    print(f"     Binary: {binary}")
    print(f"     Socket: {SOCK_OWN}")


def _stop_mihomo():
    try:
        result = subprocess.run(["pgrep", "-f", "--", f"-ext-ctl-unix {SOCK_OWN}"], capture_output=True, text=True, timeout=5)
        pids = [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
    except Exception:
        pids = []

    if not pids:
        print("  ℹ️  Own mihomo not running.")
        return

    for pid in pids:
        try:
            os.kill(pid, 15)
            print(f"  ✅ Stopped mihomo (pid={pid})")
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"  ⚠️  Permission denied for pid={pid}")

    time.sleep(0.5)
    if os.path.exists(SOCK_OWN):
        try:
            os.unlink(SOCK_OWN)
        except OSError:
            pass


if __name__ == "__main__":
    main()