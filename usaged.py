#!/usr/bin/env python3
"""usaged: poll subscription usage for several providers, serve it over HTTP on the tailnet.

A provider is fetch() -> [window] and optionally refresh(). fetch raises Unauthorized when
the credential is stale; the poller then calls refresh() (which shells out to the vendor CLI
so the CLI stays the only writer of its own credentials) and fetches once more.
A window is {"name": str, "used": 0..1, "resets_at": ISO-8601 UTC or None}.
"""
from __future__ import annotations
import base64, json, os, re, subprocess, sys, threading, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".local/bin")
PORT = 8787
INTERVAL = 300

class Unauthorized(Exception): pass

def env(key):
  val = None
  for line in open(os.path.join(HOME, ".env")):
    k, sep, v = line.strip().partition("=")
    if sep and k.removeprefix("export ").strip() == key: val = v.strip().strip("'\"")  # last wins, like `source`
  if val is None: raise KeyError(f"{key} not in ~/.env")
  return val

def http(method, url, headers, body=None):
  req = urllib.request.Request(url, method=method, headers={"User-Agent": "usaged", **headers},
                               data=None if body is None else json.dumps(body).encode())
  if body is not None: req.add_header("Content-Type", "application/json")
  try:
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
  except urllib.error.HTTPError as e:
    if e.code == 401: raise Unauthorized(f"{url}: 401")
    raise RuntimeError(f"{url}: HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")

def keychain(service, account=None):
  cmd = ["security", "find-generic-password", "-s", service, "-w"] + (["-a", account] if account else [])
  return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()

def run(*cmd): subprocess.run(cmd, capture_output=True, timeout=120, check=True)

def iso(ts):
  if ts is None: return None
  if isinstance(ts, (int, float)): d = datetime.fromtimestamp(ts, timezone.utc)
  else: d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
  return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def win(name, used, resets_at): return {"name": name, "used": round(used, 4), "resets_at": iso(resets_at)}

def span(mins): return f"{mins // 1440}d" if mins % 1440 == 0 else f"{mins // 60}h" if mins % 60 == 0 else f"{mins}m"

# ---- providers

class Claude:
  # `claude -p /usage` is free (no model call) and lets claude own its credentials (it moves them
  # between keychain and ~/.claude/.credentials.json on its own). on failure, refresh() and retry.
  # we parse its text output:
  #   Current session: 7% used · resets Nov 22 at 6:50pm (America/Los_Angeles)
  #   Current week (all models): 2% used · resets Nov 25 at 6pm (America/Los_Angeles)
  #   Current week (Fable): 0% used · resets Nov 25 at 6pm (America/Los_Angeles)
  id = "claude"
  LINE = re.compile(r"^Current (session|week \(([^)]+)\)): (\d+)% used(?: · resets (\w+ \d+) at (\d+)(?::(\d+))?([ap]m) \(([^)]+)\))?$", re.M)
  def fetch(self):
    r = subprocess.run([f"{BIN}/claude", "-p", "/usage", "--output-format", "json"],
                       capture_output=True, text=True, timeout=120, cwd="/tmp")
    d = json.loads(r.stdout)
    if d.get("is_error"): raise RuntimeError(f"claude /usage: {d.get('result')}")
    out = []
    for kind, scope, pct, day, hh, mm, ap, tz in self.LINE.findall(d["result"]):
      name = "5h" if kind == "session" else "7d" if scope == "all models" else f"7d {scope}"
      out.append(win(name, int(pct) / 100, self.reset(day, hh, mm, ap, tz) if day else None))
    # with a dead token /usage doesn't error: it silently prints local cost stats instead
    if not out: raise Unauthorized(f"claude /usage: no subscription usage in output: {d['result'][:200]}")
    return out
  # a real (tiny) request makes claude refresh its token; /usage and `auth status` don't
  def refresh(self): run(f"{BIN}/claude", "-p", "ok", "--model", "haiku")
  @staticmethod
  def reset(day, hh, mm, ap, tz):  # "Nov 25", "6", "", "pm", "America/Los_Angeles"; year is implied (next occurrence)
    now = datetime.now(ZoneInfo(tz))
    t = datetime.strptime(f"{day} {now.year} {hh}:{mm or '00'}{ap}", "%b %d %Y %I:%M%p").replace(tzinfo=ZoneInfo(tz))
    if t < now - timedelta(days=1): t = t.replace(year=now.year + 1)
    return t.timestamp()

class Codex:
  # one-off `codex app-server` over stdio: account/rateLimits/read refreshes auth.json itself if needed
  id = "codex"
  def fetch(self):
    p = subprocess.Popen([f"{BIN}/codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(60, p.kill); timer.start()
    try:
      for m in ({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "usaged", "version": "0"}}},
                {"method": "initialized"}, {"id": 2, "method": "account/rateLimits/read"}):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", **m}) + "\n")
      p.stdin.flush()
      for line in p.stdout:
        m = json.loads(line)
        if m.get("id") == 2: break
      else: raise RuntimeError("codex app-server exited without a response")
    finally:
      timer.cancel(); p.kill(); p.wait()
    if "error" in m: raise RuntimeError(f"codex: {m['error'].get('message')}")
    rl = m["result"]["rateLimits"]
    return [win(span(w["windowDurationMins"]), w["usedPercent"] / 100, w["resetsAt"])
            for w in (rl["primary"], rl["secondary"]) if w]

class Google:
  id = "google"
  BASE = "https://daily-cloudcode-pa.googleapis.com/v1internal:"  # agy + the IDE meter here; prod cloudcode-pa never moves
  def fetch(self):
    raw = keychain("gemini", "antigravity").removeprefix("go-keyring-base64:")
    ver = subprocess.run([f"{BIN}/agy", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    h = {"Authorization": f"Bearer {json.loads(base64.b64decode(raw))['token']['access_token']}",
         "User-Agent": f"antigravity/{ver} darwin/amd64"}  # backend 403s non-antigravity UAs
    # all gemini-* models (incl. gemini 4) draw from one shared 5h pool, mirrored into a bucket per model.
    # the Claude/GPT pool (retrieveUserQuotaSummary) is deliberately ignored.
    gem = [b for b in http("POST", self.BASE + "retrieveUserQuota", h, {})["buckets"] if b["modelId"].startswith("gemini")]
    worst = min(gem, key=lambda b: b["remainingFraction"])
    return [win("5h", 1 - worst["remainingFraction"], worst.get("resetTime"))]
  def refresh(self): run(f"{BIN}/agy", "models")

class OpenCode:
  id = "opencode"
  def fetch(self):
    u = http("GET", "https://opencode.ai/zen/go/v1/usage", {"Authorization": f"Bearer {env('OPENCODE_API_KEY')}"})["usage"]
    return [win(n, u[k]["percent"] / 100, u[k]["resetsAt"]) for k, n in (("rolling", "5h"), ("weekly", "7d"), ("monthly", "30d"))]

class Kimi:
  id = "kimi"
  def fetch(self):
    u = http("GET", "https://api.kimi.com/coding/v1/usages", {"Authorization": f"Bearer {env('KIMI_CODE_API_KEY')}"})["usages"]
    return [win(n, u[k]["used_ratio"], u[k]["reset_time"]) for k, n in (("limit_5h", "5h"), ("limit_7d", "7d"))]

PROVIDERS = [Claude(), Codex(), Google(), OpenCode(), Kimi()]

# ---- poller + server

state = {p.id: {"id": p.id, "windows": [], "fetched_at": None, "error": None} for p in PROVIDERS}
lock = threading.Lock()

def poll(p):
  try:
    try: windows = p.fetch()
    except Unauthorized:
      if not hasattr(p, "refresh"): raise
      p.refresh()
      windows = p.fetch()
    s = {"windows": windows, "fetched_at": iso(time.time()), "error": None}
  except Exception as e:
    s = {"error": f"{type(e).__name__}: {e}"}  # keep last good windows; fetched_at shows staleness
    print(f"{iso(time.time())} {p.id}: {s['error']}", file=sys.stderr, flush=True)
  with lock: state[p.id].update(s)

def poller():
  while True:
    ts = [threading.Thread(target=poll, args=(p,)) for p in PROVIDERS]
    for t in ts: t.start()
    for t in ts: t.join()
    time.sleep(INTERVAL)

class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
    if self.path != "/usage": return self.send_error(404)
    with lock: body = json.dumps({"providers": [state[p.id] for p in PROVIDERS]}).encode()
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)
  def log_message(self, *a): pass

if __name__ == "__main__":
  host = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, check=True).stdout.split()[0]
  threading.Thread(target=poller, daemon=True).start()
  print(f"usaged on http://{host}:{PORT}/usage", file=sys.stderr, flush=True)
  ThreadingHTTPServer((host, PORT), Handler).serve_forever()
