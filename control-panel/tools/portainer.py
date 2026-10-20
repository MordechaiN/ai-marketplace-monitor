"""Portainer API helper for maintaining the stack. Configure with environment variables:
  PORTAINER_URL       e.g. https://portainer.example.com/api
  PORTAINER_KEY_FILE  a mode-600 file holding one header line "X-API-Key: <token>"; passed to curl with -H @file, so the token never
                      appears in a command line, output or log
  PORTAINER_STACK_ID, PORTAINER_ENDPOINT_ID   numeric ids from the Portainer UI
Normal TLS verification, never -k.
Usage as a module: from portainer import api, exec_in, stack_env, update_stack"""
import json, os, struct, subprocess

BASE = os.environ.get("PORTAINER_URL", "https://portainer.example.com/api").rstrip("/")
KEY = os.environ.get("PORTAINER_KEY_FILE", os.path.expanduser("~/.portainer/api-key.hdr"))
STACK, ENDPOINT = int(os.environ.get("PORTAINER_STACK_ID", "0")), int(os.environ.get("PORTAINER_ENDPOINT_ID", "0"))


def api(method, path, body=None, raw=False, timeout=300):
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-H", f"@{KEY}", "-X", method, BASE + path]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True).stdout
    return out if raw else (json.loads(out) if out.strip() else {})


def container_id(name):
    for c in api("GET", f"/endpoints/{ENDPOINT}/docker/containers/json?all=1"):
        if "/" + name in c["Names"]:
            return c["Id"]


def exec_in(name, cmd):
    """Run a command (list) inside one of OUR containers through Portainer; returns decoded output."""
    i = container_id(name)
    e = api("POST", f"/endpoints/{ENDPOINT}/docker/containers/{i}/exec", {"AttachStdout": True, "AttachStderr": True, "Cmd": cmd})
    raw = api("POST", f"/endpoints/{ENDPOINT}/docker/exec/{e['Id']}/start", {"Detach": False, "Tty": False}, raw=True)
    out, p = b"", 0
    while p + 8 <= len(raw):
        n = struct.unpack(">I", raw[p + 4:p + 8])[0]
        out += raw[p + 8:p + 8 + n]
        p += 8 + n
    return out.decode(errors="replace")


def stack_env():
    return {e["name"]: e["value"] for e in api("GET", f"/stacks/{STACK}")["Env"]}


def update_stack(compose_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stack", "docker-compose.yml"), env_changes=None, remove=()):
    """PUT the stack with the current variables (read from Portainer, so none are lost) plus changes."""
    env = stack_env()
    env.update(env_changes or {})
    for k in remove:
        env.pop(k, None)
    r = api("PUT", f"/stacks/{STACK}?endpointId={ENDPOINT}", {"stackFileContent": open(compose_path).read(), "env": [{"name": k, "value": v} for k, v in env.items()], "prune": False, "pullImage": False})
    return {k: r.get(k) for k in ("Id", "Name", "Status", "message", "details")}
