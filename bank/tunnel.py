"""A Cloudflare quick tunnel to the local bank, so Freya's cloud can call it. No account needed.

    url, proc = start(8787)      # https://<words>.trycloudflare.com
    ...
    stop(proc)                   # kills by pid
"""
from collections import deque
import re, subprocess, threading, time, urllib.error, urllib.request

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED = "/usr/bin/cloudflared"


def parse_url(line):
    m = URL_RE.search(line)
    return m[0] if m and m[0] != "https://api.trycloudflare.com" else None


def start(port, timeout=30, ready_path="/health", ready_timeout=60):
    """Run `cloudflared tunnel --url http://localhost:<port>`, return (public_url, Popen) once the URL is printed
    and, when ready_path is set, answers through the tunnel. cloudflared logs to stderr; a thread keeps draining it."""
    proc = subprocess.Popen([CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        found, lines = [], deque(maxlen=50)

        def drain():
            for line in proc.stderr:
                lines.append(line)
                if not found and parse_url(line): found.append(parse_url(line))
        threading.Thread(target=drain, daemon=True).start()
        t0 = time.time()
        while not found and time.time() - t0 < timeout and proc.poll() is None: time.sleep(0.2)
        if not found:
            raise RuntimeError("cloudflared printed no trycloudflare.com URL within %d s:\n%s" % (timeout, "".join(list(lines)[-15:])))
        url = found[0]
        if ready_path:
            t0 = time.time()
            while True:      # the hostname takes a few seconds to resolve and route
                try:
                    urllib.request.urlopen(urllib.request.Request(url + ready_path, headers={"user-agent": "Mozilla/5.0 (bank-tunnel)"}), timeout=5).read()
                    break
                except (urllib.error.URLError, OSError):
                    if time.time() - t0 > ready_timeout: raise RuntimeError(f"{url} did not answer within {ready_timeout} s")
                    time.sleep(1)
    except BaseException:
        stop(proc)
        raise
    return url, proc


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try: proc.wait(10)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
