#!/usr/bin/env python3
"""SSH stdin adapter to a configured Pi API. Credentials stay on that host."""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 128 * 1024


class SafeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SafeError("upstream")


def fetch(base, key, route, payload=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(base + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=5 if route == "/models" else 55) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise SafeError("incomplete")
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        raise SafeError("auth" if error.code in (401, 403) else "upstream") from None


def complete(request, config=None):
    if not isinstance(request, dict) or set(request) != {"system", "input", "model"}:
        raise SafeError("input")
    if any(not isinstance(request[k], str) or "\0" in request[k] or len(request[k].encode()) > 8192 for k in ("system", "input")):
        raise SafeError("input")
    model = request["model"]
    if not isinstance(model, str) or not model or len(model) > 128:
        raise SafeError("model")
    path = Path.home() / ".pi/agent/proxy-config.json"
    config = json.loads(path.read_text()) if config is None else config
    # Do not allow config errors to forward credentials to an arbitrary server.
    host = config.get("host")
    allowed_hosts = {"127.0.0.1", "localhost"}
    allowed_hosts.update(h.strip() for h in os.environ.get("SOFT_LAUNCHER_CODEX_PROXY_HOSTS", "").split(",") if h.strip())
    if host not in allowed_hosts:
        raise SafeError("upstream")
    port = config.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise SafeError("upstream")
    key = config.get("authToken")
    if not isinstance(key, str) or "\n" in key or "\r" in key:
        raise SafeError("auth")
    base = f"http://{host}:{port}/v1"
    catalog = fetch(base, key, "/models")
    if not any(m.get("id") == model and m.get("owned_by") == "openai-codex" for m in catalog.get("data", [])):
        raise SafeError("model")
    result = fetch(base, key, "/chat/completions", {
        "model": model,
        "messages": [{"role": "system", "content": request["system"]}, {"role": "user", "content": request["input"]}],
        "stream": False, "max_tokens": 2048, "reasoning_effort": "low",
    })
    choices = result.get("choices", [])
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        raise SafeError("incomplete")
    message = choices[0].get("message", {})
    text = message.get("content")
    if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
        raise SafeError("incomplete")
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16 * 1024:
        raise SafeError("incomplete")
    return {"ok": True, "text": text.strip()}


def main():
    try:
        raw = sys.stdin.buffer.read(24 * 1024 + 1)
        if len(raw) > 24 * 1024:
            raise SafeError("input")
        answer = complete(json.loads(raw))
    except SafeError as error:
        answer = {"ok": False, "error": str(error)}
    except Exception:
        # Never print exception values, upstream bodies, config, or headers.
        answer = {"ok": False, "error": "upstream"}
    print(json.dumps(answer, ensure_ascii=False))


if __name__ == "__main__":
    main()
