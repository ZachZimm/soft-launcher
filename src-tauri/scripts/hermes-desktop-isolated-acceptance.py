#!/usr/bin/env python3
"""Send to agent through a launcher binary into a real Hermes Desktop, isolated.

Runs only inside the hidden agent workspace, started by
run-hermes-desktop-isolated-acceptance.py. Everything graphical is private:

- The launcher binary runs on the workspace's private Xvfb display under
  WebKitWebDriver. Launcher keys and pointer events are DOM events in its page.
- Hermes Desktop (Electron, X11) runs on the same private display, with a
  private userData directory, so its single-instance lock, window state and
  backend-ownership records cannot reach a host Desktop instance. The only
  synthetic input it receives is xdotool on the private display.
- The session bus is a private dbus-run-session bus.
- The handoff path is the real adapter -> `hermes desktop --skip-build
  --launcher-handoff` CLI -> Electron. In the candidate stage `hermes` is a
  documented private wrapper running a copy of the patched CLI whose
  apps/desktop/release is the candidate package; in the installed stage it is
  the real ~/.local/bin/hermes with the deployed package.
- HERMES_HOME is the real ~/.hermes (credentials are a rotating OAuth pool, so
  they are never copied). New public QA conversations are selected by
  metadata; no other conversation is read. Screenshots collapse the sidebar
  and black out its band so other conversation titles are never captured.
- A private read-only `git` stand-in refuses the Desktop updater's automatic
  `git fetch` of the Hermes checkout.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
REAL_HOME = Path(os.environ.get('SOFT_LAUNCHER_QA_REAL_HOME', str(Path.home())))
HERMES_HOME = REAL_HOME / '.hermes'
HERMES_DB = HERMES_HOME / 'state.db'
DESKTOP_LOG = HERMES_HOME / 'logs/desktop.log'
AGENT_TEXT = 'Reply exactly: soft-launcher QA ok. Use no tools.'
EXPECTED_REPLY = 'soft-launcher QA ok.'
FOLLOWUP_TEXT = 'Reply exactly: follow-up ok. Use no tools.'
FOLLOWUP_REPLY = 'follow-up ok.'
WARM_TEXT = 'Reply exactly: soft-launcher warm ok. Use no tools.'
WARM_REPLY = 'soft-launcher warm ok.'
EXACT_TEXT = '  /help is data here, not a command. Reply exactly: soft-launcher exact ok. Use no tools.\n'
EXACT_REPLY = 'soft-launcher exact ok.'
CLIP_SENTINEL = 'QA-CLIPBOARD-SENTINEL-4c1e public text that must never reach Hermes'
LAB = os.environ.get('SOFT_LAUNCHER_QA_CLEF_HOST', '127.0.0.1:11434')
SIDEBAR_MASK_PX = 250  # Desktop sidebar default width is 237 px (fresh private userData)
GIT_READ_ONLY = ('rev-parse', 'status', 'log', 'branch', 'symbolic-ref', 'rev-list', 'merge-base', 'show', 'diff',
                 'ls-files', 'describe', 'remote', 'config', 'for-each-ref', 'cat-file', 'worktree', 'version', '--version')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def request(base, method, path, data=None, timeout=30):
    raw = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base + path, data=raw, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            value = json.load(response)['value']
    except urllib.error.HTTPError as error:
        value = json.loads(error.read())['value']
    if isinstance(value, dict) and value.get('error') and 'stacktrace' in value:
        raise RuntimeError(f"WebDriver {value['error']}: {value.get('message', '')}")
    return value


def proc_env(pid):
    try:
        raw = Path(f'/proc/{pid}/environ').read_bytes()
    except OSError:
        return {}
    out = {}
    for item in raw.split(b'\0'):
        key, _, value = item.partition(b'=')
        if key:
            out[key.decode(errors='replace')] = value.decode(errors='replace')
    return out


def proc_argv(pid):
    try:
        return [a.decode(errors='replace') for a in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if a]
    except OSError:
        return []


def proc_ppid(pid):
    try:
        return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def proc_exe(pid):
    try:
        return os.readlink(f'/proc/{pid}/exe')
    except OSError:
        return None


def marked_pids(marker):
    found = []
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal() and int(entry.name) != os.getpid():
            if proc_env(int(entry.name)).get('SL_HD_QA_RUN') == marker:
                found.append(int(entry.name))
    return sorted(found)


def write_exec(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o700)


def make_fakes(qa):
    """Public stand-ins: context collectors for the launcher, and a read-only git."""
    bin_dir = qa / 'bin'
    bin_dir.mkdir()
    (qa / 'clipboard.txt').write_text(CLIP_SENTINEL)
    write_exec(bin_dir / 'hyprctl', f"""#!/bin/sh
echo "hyprctl $1" >> '{qa}/calls.log'
if [ "$1" = activewindow ]; then printf '%s' '{{"class":"qa-public-editor","title":"Public QA document","address":"0x51a","pid":1}}'; exit 0; fi
if [ "$1" = clients ]; then printf '[]'; exit 0; fi
exit 1
""")
    write_exec(bin_dir / 'wl-paste', f"""#!/bin/sh
echo "wl-paste $*" >> '{qa}/calls.log'
for a in "$@"; do [ "$a" = --list-types ] && {{ echo 'text/plain;charset=utf-8'; exit 0; }}; done
cat '{qa}/clipboard.txt'
""")
    write_exec(bin_dir / 'wl-copy', f"""#!/bin/sh
echo "wl-copy $*" >> '{qa}/calls.log'
cat > /dev/null
""")
    allowed = ' '.join(GIT_READ_ONLY)
    write_exec(bin_dir / 'git', f"""#!/bin/sh
# Read-only git for QA: the Desktop updater must not fetch the real Hermes checkout.
sub=""
skip=0
for a in "$@"; do
  if [ $skip = 1 ]; then skip=0; continue; fi
  case "$a" in -C|-c|--git-dir|--work-tree) skip=1; continue;; -*) continue;; esac
  sub="$a"; break
done
for ok in {allowed}; do
  if [ "$sub" = "$ok" ]; then exec /usr/bin/git "$@"; fi
done
echo "git-refused $sub" >> '{qa}/git.log'
exit 128
""")
    return bin_dir


HOOK = r"""
const qa=window.__hf??{requests:[],executions:[],hides:0};window.__hf=qa;
const originalFetch=window.__hfFetch??window.fetch;window.__hfFetch=originalFetch;
window.fetch=async function(...args){
 const target=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
 const ipc=target.protocol==='ipc:'||target.hostname==='ipc.localhost';
 if(!ipc){
  const row={host:target.host,path:target.pathname};qa.requests.push(row);
  if(target.port==='7331'||(target.port==='11434'&&target.host!=="%CLEF_HOST%")){row.refused=true;throw new TypeError('Failed to fetch (refused by isolated QA)');}
  try{const r=await originalFetch(...args);row.status=r.status;return r;}catch(e){row.error=String(e).slice(0,80);throw e;}
 }
 if(target.pathname==='/hide_window')qa.hides++;
 if(target.pathname==='/execute'){
  const r=JSON.parse(args[1].body)?.request??{};
  const input=r.args?.input;
  const row={actionId:r.actionId,keys:Object.keys(r.args??{}).sort(),token:!!r.confirmationToken,inputSha256:null,inputLength:typeof input==='string'?input.length:null,at:Date.now()};
  if(typeof input==='string'){const d=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(input));row.inputSha256=[...new Uint8Array(d)].map(b=>b.toString(16).padStart(2,'0')).join('');}
  qa.executions.push(row);
  let resp;
  try{resp=await originalFetch(...args);}catch(e){row.status='fetch-error';throw e;}
  try{const v=await resp.clone().json();row.status=v?.status??'error';row.message=typeof v==='string'?v.slice(0,160):(v?.message??'').slice(0,160);row.hasText=typeof v?.text==='string';row.doneAt=Date.now();}catch(e){}
  return resp;
 }
 return originalFetch(...args);
};
if(!window.__hfES){window.__hfES=window.EventSource;window.EventSource=function(url,...rest){const t=new URL(url,location.href);qa.requests.push({host:t.host,path:t.pathname,refused:t.port==='7331',sse:true});if(t.port==='7331')throw new TypeError('EventSource refused by isolated QA');return new window.__hfES(url,...rest);};}
return true;
"""
FEEDBACK = "return [...document.querySelectorAll('.feedback')].map(e=>e.innerText.trim()).filter(Boolean).join(' | ')"
COUNT = "const t=document.querySelector('.state[role=status]')?.title??'';const m=t.match(/over (\\d+) decisions/);return m?Number(m[1]):0"


def hermes_db():
    return sqlite3.connect(f'file:{HERMES_DB}?mode=ro', uri=True, timeout=2)


def new_desktop_session_ids(since):
    """Metadata only: ids of desktop-source sessions started at/after `since`."""
    con = hermes_db()
    try:
        return [r[0] for r in con.execute("select id from sessions where source='desktop' and started_at >= ? order by started_at", (since,))]
    finally:
        con.close()


def session_detail(sid):
    """Read exactly one QA session chosen by id (from Desktop's own ACK log line)."""
    con = hermes_db()
    try:
        row = con.execute('select model, billing_provider, started_at, message_count, tool_call_count, cwd from sessions where id=?', (sid,)).fetchone()
        if not row:
            return None
        model, provider, started, count, tools, cwd = row
        users = [c or '' for (c,) in con.execute("select content from messages where session_id=? and role='user' order by id", (sid,))]
        replies = con.execute("select content, finish_reason, tool_calls from messages where session_id=? and role='assistant' order by id", (sid,)).fetchall()
        usage = con.execute('select model, billing_provider, api_call_count from session_model_usage where session_id=?', (sid,)).fetchall()
        return {'id': sid, 'model': model, 'billingProvider': provider, 'startedAt': started, 'messageCount': count, 'toolCallCount': tools,
                'cwd': cwd, 'users': users, 'userSha256': [sha(u.encode()) for u in users],
                'assistant': [{'content': (c or '')[:300], 'finishReason': f, 'toolCalls': bool(t and t not in ('[]', 'null'))} for c, f, t in replies],
                'usage': [{'model': m, 'billingProvider': b, 'apiCalls': n} for m, b, n in usage]}
    finally:
        con.close()


def handoff_log_lines(since_iso):
    """Only Desktop's own `[launcher-handoff]` lines stamped at/after `since_iso`.

    Filtering by timestamp, not offset: Desktop writes its log asynchronously
    and may rotate it at boot."""
    try:
        data = DESKTOP_LOG.read_bytes().decode(errors='replace')
    except OSError:
        return []
    return [line.strip() for line in data.splitlines() if '[launcher-handoff]' in line and line[1:25] >= since_iso]


def accepted_lines(since_iso, count, seconds=8):
    deadline = time.monotonic() + seconds
    while True:
        lines = [l for l in handoff_log_lines(since_iso) if ' accepted ' in l]
        if len(lines) >= count or time.monotonic() > deadline:
            return lines
        time.sleep(.25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('candidate', 'installed'), required=True)
    parser.add_argument('--display', required=True)
    parser.add_argument('--workspace-runtime', type=Path, required=True)
    parser.add_argument('--profile-dir', type=Path, required=True)
    parser.add_argument('--marker', required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--catalogue', type=Path, required=True)
    parser.add_argument('--desktop-exe', type=Path, required=True, help='Electron executable the CLI must launch')
    parser.add_argument('--shots', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-pending-ui', action='store_true', help='Diagnostic only: skip in-flight UI checks (never acceptance)')
    args = parser.parse_args()

    ws_rt = args.workspace_runtime.resolve(strict=True)
    qa = args.profile_dir.resolve()
    rt = qa / 'rt'
    refusals = []
    if os.environ.get('SL_HD_QA') != '1' or os.environ.get('SL_HD_QA_RUN') != args.marker:
        refusals.append('not started by the isolated runner')
    for name in ('WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE', 'SWAYSOCK', 'WAYLAND_SOCKET'):
        if os.environ.get(name):
            refusals.append(f'{name} inherited from the host')
    if os.environ.get('DISPLAY') != args.display or not args.display.startswith(':') or args.display in (':0', ':1'):
        refusals.append('DISPLAY is not the workspace display')
    if ws_rt.parent != Path(f'/run/user/{os.getuid()}/agent-workspace-linux') or qa.parent != ws_rt:
        refusals.append('profile is not inside the workspace runtime')
    if Path(os.environ.get('XDG_RUNTIME_DIR', '/')).resolve() != rt:
        refusals.append('XDG_RUNTIME_DIR is not private')
    if Path(os.environ.get('HERMES_DESKTOP_USER_DATA_DIR', '/')).resolve().parent != qa:
        refusals.append('Desktop userData is not private')
    if os.environ.get('HERMES_HOME') != str(HERMES_HOME):
        refusals.append('HERMES_HOME is not pinned')
    bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
    if not bus.startswith('unix:path=/tmp/dbus-') or f'/run/user/{os.getuid()}/bus' in bus:
        refusals.append('session bus is not the private dbus-run-session bus')
    hermes_cmd = Path(os.environ.get('HOME', '/')) / '.local/bin/hermes'
    if args.stage == 'installed' and (os.environ.get('HOME') != str(REAL_HOME)):
        refusals.append('installed stage must use the real HOME and ~/.local/bin/hermes')
    if args.stage == 'candidate' and os.environ.get('HOME') == str(REAL_HOME):
        refusals.append('candidate stage must use the private wrapper HOME')
    if refusals:
        print(json.dumps({'passed': False, 'refused': refusals}))
        return 2

    shots_dir = args.shots
    shots_dir.mkdir(parents=True, exist_ok=True)
    for old in shots_dir.glob('*.png'):
        old.unlink()
    catalogue = qa / 'catalogue.toml'
    shutil.copyfile(args.catalogue, catalogue)
    bin_dir = make_fakes(qa)
    receipts = qa / 'state/soft-launcher/hermes-desktop'
    evidence = {
        'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'stage': args.stage,
        'binary': str(args.binary), 'binarySha256': sha(args.binary.read_bytes()),
        'adapter': str(args.adapter), 'adapterSha256': sha(args.adapter.read_bytes()),
        'hermesCommand': str(hermes_cmd), 'hermesCommandIsWrapper': args.stage == 'candidate',
        'hermesCommandText': hermes_cmd.read_text()[:600] if args.stage == 'candidate' else None,
        'desktopExe': str(args.desktop_exe), 'desktopExeSha256': sha(args.desktop_exe.read_bytes()),
        'desktopAsarSha256': sha((args.desktop_exe.parent / 'resources/app.asar').read_bytes()),
        'catalogueCopySha256': sha(catalogue.read_bytes()),
        'isolation': {'display': args.display, 'privateRuntime': str(rt), 'privateBus': bus.split(',')[0],
                      'desktopUserData': os.environ['HERMES_DESKTOP_USER_DATA_DIR'], 'hermesHome': os.environ['HERMES_HOME']},
        'checks': [], 'passed': False,
    }
    driver = session = native_pid = None
    base = ''
    shots = []
    log_offset = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:23] + 'Z'
    run_started = time.time()

    def note(label, **extra):
        evidence['checks'].append({'check': label, **extra})

    def fail(label, **extra):
        evidence['failure'] = {'check': label, **extra}
        raise AssertionError(label)

    def desktop_procs():
        out = {}
        for p in marked_pids(args.marker):
            exe = proc_exe(p)
            if exe and Path(exe) == args.desktop_exe.resolve():
                out[p] = proc_argv(p)
        return out

    def desktop_primary():
        procs = desktop_procs()
        return sorted(p for p, a in procs.items() if not any(x.startswith('--type=') for x in a))

    def x_shot(name):
        """Private-display screenshot with the sidebar band blacked out before saving."""
        from PIL import Image, ImageDraw
        raw = qa / f'{name}.raw.png'
        r = subprocess.run(['import', '-display', args.display, '-window', 'root', str(raw)], capture_output=True, timeout=15)
        if r.returncode != 0:
            return False
        left = top = 0
        win = desktop_window()
        if win:
            geo = subprocess.run(['xdotool', 'getwindowgeometry', '--shell', win], capture_output=True, text=True, timeout=5).stdout
            g = dict(l.split('=', 1) for l in geo.split() if '=' in l)
            left, top = max(0, int(g.get('X', 0))), max(0, int(g.get('Y', 0)))
        with Image.open(raw) as img:
            img = img.convert('RGB')
            # Decide automatically, before anything is saved or viewed: a collapsed
            # sidebar leaves its band blank below the messages; any text there means
            # it is open, and the band stays blacked out.
            band = img.crop((left, top + 350, left + SIDEBAR_MASK_PX, min(img.height, top + 800))).convert('L')
            collapsed = bool(win) and band.getextrema()[0] >= 200
            if not collapsed:
                ImageDraw.Draw(img).rectangle([0, 0, left + SIDEBAR_MASK_PX, img.height], fill=(0, 0, 0))
            evidence.setdefault('screenshotMasking', {})[name] = 'sidebar collapsed (band blank), unmasked' if collapsed else 'sidebar band blacked out'
            img.save(shots_dir / f'{name}.png')
        raw.unlink()
        shots.append(str((shots_dir / f'{name}.png').relative_to(ROOT)))
        return True

    def desktop_window():
        for p in desktop_primary():
            out = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(p)], capture_output=True, text=True, timeout=5).stdout.split()
            if out:
                return out[-1]
        out = subprocess.run(['xdotool', 'search', '--onlyvisible', '--class', 'Hermes'], capture_output=True, text=True, timeout=5).stdout.split()
        return out[-1] if out else None

    def xkey(win, *keys):
        # XTEST on the private display only; --window would use XSendEvent, which Chromium ignores.
        subprocess.run(['xdotool', 'windowfocus', '--sync', win], capture_output=True, timeout=5)
        subprocess.run(['xdotool', 'key', '--clearmodifiers', *keys], capture_output=True, timeout=5)

    def wait_reply(sid, turn_index, expected, seconds=150):
        deadline = time.monotonic() + seconds
        detail = None
        while time.monotonic() < deadline:
            detail = session_detail(sid)
            replies = [a for a in (detail or {}).get('assistant', []) if a['content'].strip()]
            # Model output may drop the final period; record exactness separately.
            if detail and len(detail['users']) > turn_index and len(replies) > turn_index \
                    and expected.rstrip('.') in replies[turn_index]['content']:
                detail['replyExact'] = replies[turn_index]['content'].strip() == expected
                return detail, round(seconds - (deadline - time.monotonic()), 1)
            time.sleep(1)
        return detail, None

    def run_adapter(text):
        """Feed the adapter exactly as native does: one JSON object on stdin."""
        env = {**os.environ}
        r = subprocess.run([sys.executable, '-I', str(args.adapter)], input=json.dumps({'text': text}).encode(), env=env,
                           capture_output=True, timeout=55, cwd=str(qa))
        try:
            return json.loads(r.stdout)
        except ValueError:
            return {'raw': r.stdout.decode(errors='replace')[:300], 'code': r.returncode}

    def raw_request(fields):
        """Protocol-level client for replay/expiry checks: same private shape the adapter makes."""
        folder = Path(__import__('tempfile').mkdtemp(prefix='slhd-', dir=rt))
        sock_path = folder / 'ack.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sock_path))
        sock_path.chmod(0o600)
        listener.listen(1)
        listener.settimeout(20)
        req = folder / 'request.json'
        body = {'version': 1, 'requestId': str(uuid.uuid4()), 'text': AGENT_TEXT, 'replySocket': str(sock_path),
                'expiresAt': int(time.time() * 1000 + 42_000), 'provider': 'openai-codex', 'model': 'gpt-6.1-sol', **fields}
        fd = os.open(req, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(body, handle)
        child = subprocess.Popen([str(hermes_cmd), 'desktop', '--skip-build', '--launcher-handoff', str(req)], cwd=os.environ['HOME'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            conn, _ = listener.accept()
            with conn:
                ack = json.loads(conn.makefile('rb').readline())
        except (socket.timeout, ValueError) as error:
            ack = {'error': f'no ack: {type(error).__name__}'}
        finally:
            listener.close()
            child.wait(timeout=20)
            shutil.rmtree(folder, ignore_errors=True)
        return body['requestId'], ack

    try:
        # ---- Launcher on the private X display ----------------------------------------
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {k: v for k, v in os.environ.items()}
        env.update({
            'TAURI_WEBVIEW_AUTOMATION': 'true', 'GDK_BACKEND': 'x11',
            'SOFT_LAUNCHER_CATALOGUE': str(catalogue),
            'SOFT_LAUNCHER_HERMES_ADAPTER': str(args.adapter),
            'SOFT_LAUNCHER_CODEX_SSH_HOST': 'lab', 'SOFT_LAUNCHER_CODEX_MODEL': 'gpt-6.1-sol',
            'SOFT_LAUNCHER_CLEF_URL': f'http://{LAB}',
            'PATH': f"{bin_dir}:{os.environ['PATH']}",
            'XDG_DATA_DIRS': ':'.join([str(REAL_HOME / '.local/share'), '/usr/local/share', '/usr/share']),
        })
        os.environ['PATH'] = env['PATH']
        driver = subprocess.Popen(['WebKitWebDriver', '--host=127.0.0.1', f'--port={port}'], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(100):
            try:
                request(base, 'GET', '/status', timeout=1)
                break
            except (OSError, RuntimeError):
                time.sleep(.05)
        created = request(base, 'POST', '/session', {'capabilities': {'alwaysMatch': {'webkitgtk:browserOptions': {'binary': str(args.binary), 'args': ['--hidden']}}}})
        session = created['sessionId']
        native_pid = created['capabilities'].get('webkit:processID')
        if not native_pid or proc_env(native_pid).get('SL_HD_QA_RUN') != args.marker:
            fail("launcher process is this run's own instance")
        note('launcher binary started privately under WebDriver', exe=proc_exe(native_pid))

        def js(script):
            return request(base, 'POST', f'/session/{session}/execute/sync', {'script': script, 'args': []}, timeout=70)

        def wait(script, label, seconds=15):
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                if js(script):
                    note(label, ms=round((time.monotonic() - start) * 1000))
                    return True
                time.sleep(.08)
            fail(label, feedback=js(FEEDBACK)[:300])

        def key(name, hold_ms=0):
            js(f"window.dispatchEvent(new KeyboardEvent('keydown',{{key:{json.dumps(name)},bubbles:true}}));return true")
            time.sleep(max(hold_ms, 40) / 1000)
            js(f"window.dispatchEvent(new KeyboardEvent('keyup',{{key:{json.dumps(name)},bubbles:true}}));return true")
            time.sleep(.08)

        def wd_shot(name):
            png = base64.b64decode(request(base, 'GET', f'/session/{session}/screenshot'))
            (shots_dir / f'{name}.png').write_bytes(png)
            shots.append(str((shots_dir / f'{name}.png').relative_to(ROOT)))

        def type_input(text):
            js("const i=document.querySelector('#launcher-input');i.focus();i.value=" + json.dumps(text) + ";i.dispatchEvent(new Event('input',{bubbles:true}));return true")

        def target_agent_card(text, label):
            before = js(COUNT)
            type_input(text)
            wait(f"const n=(()=>{{{COUNT}}})();const s=document.querySelector('.state[role=status]');return n>{before}&&s&&s.textContent.includes('ready')&&!!document.querySelector('#launcher-card-send_to_agent')",
                 f'{label}: lab Clef decided and offered Send to agent', seconds=190)
            card = js("const c=document.querySelector('#launcher-card-send_to_agent');return c?{label:c.getAttribute('aria-label'),primed:c.classList.contains('primed')}:null")
            if not card or card['primed'] or 'needs confirmation' not in card['label'] or '→ Hermes Desktop' not in card['label']:
                fail(f'{label}: card unprimed, → Hermes Desktop, needs confirmation', card=card)
            js("const c=document.querySelector('#launcher-card-send_to_agent');const r=c.getBoundingClientRect();const o={bubbles:true,button:0,pointerId:1,clientX:r.left+r.width/2,clientY:r.top+r.height/2};c.dispatchEvent(new PointerEvent('pointerdown',o));c.dispatchEvent(new PointerEvent('pointerup',o));return true")
            time.sleep(.1)
            if not js("const c=document.querySelector('#launcher-card-send_to_agent');return c.classList.contains('target')&&c.classList.contains('explicit')"):
                fail(f'{label}: Send to agent explicitly targeted')
            return card

        def agent_execs():
            return [e for e in js('return window.__hf.executions') if e['actionId'] == 'send_to_agent']

        def show():
            subprocess.run([str(args.binary), '--toggle'], env={k: v for k, v in env.items() if k != 'TAURI_WEBVIEW_AUTOMATION'},
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            time.sleep(.4)

        wait("return !!window.__TAURI_INTERNALS__&&!!document.querySelector('#launcher-input')", 'real Tauri webview mounted (hidden start)')
        origin = js('return location.origin')
        if origin in ('http://127.0.0.1:5173', 'http://localhost:5173'):
            fail('embedded production assets', origin=origin)
        js(HOOK.replace('"%CLEF_HOST%"', json.dumps(LAB)))
        # soft-if's bridge captures fetch at mount; rebuild the runtime behind the hook while hidden.
        js("window.dispatchEvent(new KeyboardEvent('keydown',{key:',',ctrlKey:true,bubbles:true}));return true")
        wait("return !!document.querySelector('.settings')", 'settings open while hidden (Ctrl+comma in page)')
        js("[...document.querySelectorAll('.presets button')].find(b=>b.textContent.includes('Clef on lab')).click();return true")
        time.sleep(.1)
        js('window.__hf.requests=[];return true')
        js("[...document.querySelectorAll('.settings button')].find(b=>b.textContent.includes('Apply and reconnect')).click();return true")
        time.sleep(.5)
        wait("return !!document.querySelector('#launcher-input')&&!document.querySelector('.settings')", 'runtime rebuilt behind the hook while hidden', seconds=10)
        show()
        if desktop_primary():
            fail('no Desktop instance before the first handoff (cold start)')
        note('embedded production assets; one private launcher; no Desktop running (cold)', origin=origin)

        # ---- Empty input never offers a send -------------------------------------------
        type_input('   ')
        time.sleep(1.2)
        empty_card = js("return !!document.querySelector('#launcher-card-send_to_agent')")
        if empty_card or agent_execs():
            fail('blank input offers no Send to agent and calls nothing')
        note('blank input: no Send to agent card, no native call')

        # ---- Cancel at the confirmation -------------------------------------------------
        target_agent_card(AGENT_TEXT, 'cancel')
        key('Enter', hold_ms=260)
        wait("return document.body.innerText.includes('Send to Hermes Desktop?')", 'cancel: native required confirmation; panel shown')
        panel = js("return document.querySelector('[role=alertdialog]').innerText")
        want = (AGENT_TEXT, 'Clipboard is not sent', 'Hermes Desktop on this computer, as a new conversation', 'Press Enter again to send')
        if not all(w in panel for w in want):
            fail('confirmation names Hermes Desktop, shows exact text and clipboard exclusion', panel=panel[:400])
        note('confirmation panel: "Send to Hermes Desktop?", exact text, "→ Hermes Desktop on this computer, as a new conversation", "Clipboard is not sent"')
        wd_shot('01-launcher-confirmation')
        key('Escape')
        time.sleep(.6)
        if js("return document.body.innerText.includes('Send to Hermes Desktop?')"):
            fail('Escape dismissed the confirmation')
        time.sleep(2)
        first = agent_execs()
        if len(first) != 1 or first[0]['status'] != 'confirmationRequired' or first[0]['token']:
            fail('cancel: only the confirmation request reached native', executions=first)
        if desktop_procs() or (receipts.exists() and any(receipts.iterdir())) or list(rt.glob('slhd-*')):
            fail('cancel: no Desktop, request directory or receipt')
        note('cancel: Escape at confirmation sent nothing; no Desktop process, request directory or receipt')

        # ---- Cold handoff through the launcher ------------------------------------------
        if not js("return !!document.querySelector('#launcher-input')"):
            show()
        target_agent_card(AGENT_TEXT, 'cold')
        key('Enter', hold_ms=260)
        wait("return document.body.innerText.includes('Press Enter again to send')", 'cold: native required confirmation (armed)')
        time.sleep(.5)
        if desktop_procs():
            fail('cold: no Desktop before the second Enter')
        cold_at = time.time()
        key('Enter')
        if not args.skip_pending_ui:
          wait("return document.body.innerText.includes('Sending to Hermes Desktop')", 'cold: pending line "Sending to Hermes Desktop…" while Desktop starts', seconds=5)
        if not args.skip_pending_ui and js("return document.body.innerText.includes('Press Enter again')||!!document.querySelector('[role=alertdialog]')"):
            fail('cold: no confirmation prompt while the send is in flight')
        wd_shot('02-launcher-pending')
        key('Enter')
        key('Enter')
        if not args.skip_pending_ui:
          key('Escape')
        if args.skip_pending_ui:
            evidence['diagnosticOnly'] = 'in-flight UI checks skipped'
        elif js("return document.body.innerText.includes('Cancelled')") or not js("return document.body.innerText.includes('Sending to Hermes Desktop')"):
            fail('cold: Escape while sending claims nothing; pending line stays')
        else:
            note('cold: in-flight send shows only "Sending to Hermes Desktop…"; extra Enters and Escape change nothing')
        wait("return window.__hf.executions.some(e=>e.actionId==='send_to_agent'&&e.token&&e.status)", 'cold: token-bound request answered', seconds=60)
        sent = [e for e in agent_execs() if e['token']]
        if len(sent) != 1:
            fail('cold: extra Enters while pending dispatched nothing more', executions=agent_execs())
        sent = sent[0]
        if not (sent['status'] == 'executed' and sent['message'] == 'Sent to Hermes Desktop' and not sent['hasText']
                and sent['inputSha256'] == sha(AGENT_TEXT.encode())):
            fail('cold: native acked "Sent to Hermes Desktop" for the exact text', execution=sent, feedback=js(FEEDBACK)[:300])
        cold_ms = sent['doneAt'] - sent['at']
        note('cold: one token-bound send despite two extra Enters while pending; "Sent to Hermes Desktop", no text', handoffMs=cold_ms)
        accepted = accepted_lines(log_offset, 1)
        if len(accepted) != 1:
            fail('cold: Desktop logged exactly one accepted handoff', lines=handoff_log_lines(log_offset))
        cold_rid, cold_sid = accepted[0].split('[launcher-handoff] ', 1)[1].split()[0], accepted[0].split()[-1]
        primary = desktop_primary()
        if len(primary) != 1:
            fail('cold: one private Desktop primary', primary=primary)
        primary = primary[0]
        cli = [p for p in marked_pids(args.marker) if any(a == 'desktop' for a in proc_argv(p)) and '--launcher-handoff' in proc_argv(p)]
        argv = proc_argv(primary)
        handoff_arg = [a for a in argv if a.startswith('--hermes-launcher-handoff=')]
        if not handoff_arg or not handoff_arg[0].split('=', 1)[1].startswith(str(rt / 'slhd-')):
            fail('cold: real CLI started the expected Electron with the private request path', argv=argv[:6])
        if proc_env(primary).get('HERMES_DESKTOP_USER_DATA_DIR') != os.environ['HERMES_DESKTOP_USER_DATA_DIR']:
            fail('cold: Desktop uses the private userData')
        evidence['cold'] = {'requestId': cold_rid, 'sessionId': cold_sid, 'desktopPrimary': primary,
                            'desktopArgv': [a if not a.startswith('--hermes-launcher-handoff=') else '--hermes-launcher-handoff=<private request>' for a in argv],
                            'cliAlive': [{'pid': p, 'argv': [Path(a).name if a.startswith('/') else a for a in proc_argv(p)][:6]} for p in cli]}
        note('cold: CLI `desktop --skip-build --launcher-handoff` launched the expected Electron with private userData; one primary',
             exe=proc_exe(primary))
        time.sleep(1)
        receipt = json.loads((receipts / f'{cold_rid}.json').read_text())
        if receipt.get('status') != 'accepted' or set(receipt) - {'version', 'requestId', 'status', 'at'} or list(rt.glob('slhd-*')):
            fail('cold: receipt accepted without text; request directory removed', receipt=receipt)
        note('cold: adapter receipt {requestId,status:accepted,at} only; private request directory removed')

        detail, secs = wait_reply(cold_sid, 0, EXPECTED_REPLY)
        new_ids = new_desktop_session_ids(cold_at - 1)
        if new_ids != [cold_sid]:
            fail('cold: exactly one new Desktop conversation, the acknowledged one', newSessions=len(new_ids))
        replies = [a for a in detail['assistant'] if a['content'].strip()]
        # Hermes's auxiliary title call may use another model; the turn itself must be the per-session pick.
        model_ok = 'gpt-6.1-sol' in (detail['model'] or '') and detail['billingProvider'] in (None, '', 'openai-codex') \
            and any('gpt-6.1-sol' in (u['model'] or '') and u['billingProvider'] in (None, '', 'openai-codex') for u in detail['usage'])
        if not (secs and detail['users'] == [AGENT_TEXT] and CLIP_SENTINEL not in detail['users'][0] and replies
                and replies[0]['content'].strip().rstrip('.') == EXPECTED_REPLY.rstrip('.') and not any(a['toolCalls'] for a in detail['assistant'])
                and (detail['toolCallCount'] or 0) == 0 and model_ok):
            fail('cold: actual Desktop reply for the exact turn, per-session gpt-6.1-sol, no tools', session=detail)
        evidence['coldSession'] = detail
        note('cold: actual Hermes Desktop reply "soft-launcher QA ok." to the exact typed turn; gpt-6.1-sol/openai-codex; no tools',
             replySeconds=secs, sinceHandoffSeconds=round(time.time() - cold_at, 1))
        win = desktop_window()
        if not win:
            fail('cold: Desktop window visible on the private display')
        # Fill the private display: an open sidebar then sits inside the masked band
        # and the conversation lies to its right.
        subprocess.run(['xdotool', 'windowmove', '--sync', win, '0', '0'], capture_output=True, timeout=5)
        subprocess.run(['xdotool', 'windowsize', '--sync', win, '1600', '1000'], capture_output=True, timeout=5)
        time.sleep(1.5)
        x_shot('03-desktop-cold-reply')

        # ---- Follow-up typed in Desktop's own composer (private display) ---------------
        # Return focuses the composer when focus is elsewhere (composer.focus) and is a no-op on an empty one.
        xkey(win, 'Return')
        time.sleep(.4)
        subprocess.run(['xdotool', 'type', '--delay', '25', FOLLOWUP_TEXT], capture_output=True, timeout=20)
        time.sleep(.4)
        xkey(win, 'Return')
        detail, secs = wait_reply(cold_sid, 1, FOLLOWUP_REPLY)
        if not (secs and detail['users'] == [AGENT_TEXT, FOLLOWUP_TEXT]):
            fail('follow-up: typed in Desktop, answered in the same conversation', session=detail)
        evidence['coldSessionAfterFollowup'] = detail
        note('follow-up typed in Desktop got "follow-up ok." in the same conversation', replySeconds=secs)
        time.sleep(1)
        x_shot('04-desktop-followup')

        # ---- Warm handoff into the running Desktop --------------------------------------
        show()
        target_agent_card(WARM_TEXT, 'warm')
        key('Enter', hold_ms=260)
        wait("return document.body.innerText.includes('Press Enter again to send')", 'warm: native required confirmation (armed)')
        time.sleep(.5)
        warm_at = time.time()
        key('Enter')
        wait("return window.__hf.executions.filter(e=>e.actionId==='send_to_agent'&&e.token&&e.status).length>=2", 'warm: token-bound request answered', seconds=60)
        sent = [e for e in agent_execs() if e['token']]
        if len(sent) != 2 or sent[1]['status'] != 'executed' or sent[1]['message'] != 'Sent to Hermes Desktop' or sent[1]['inputSha256'] != sha(WARM_TEXT.encode()):
            fail('warm: one more "Sent to Hermes Desktop" for the exact text', executions=sent)
        warm_ms = sent[1]['doneAt'] - sent[1]['at']
        if desktop_primary() != [primary]:
            fail('warm: same Desktop primary; second instance forwarded and exited', primary=desktop_primary())
        accepted = accepted_lines(log_offset, 2)
        if len(accepted) != 2:
            fail('warm: Desktop logged the second accepted handoff', lines=handoff_log_lines(log_offset))
        warm_sid = accepted[1].split()[-1]
        detail, secs = wait_reply(warm_sid, 0, WARM_REPLY)
        if new_desktop_session_ids(warm_at - 1) != [warm_sid] or not secs or detail['users'] != [WARM_TEXT]:
            fail('warm: one new conversation with the exact turn and a reply', session=detail)
        evidence['warmSession'] = detail
        note('warm: running Desktop received the handoff through second-instance (same primary), new conversation, reply',
             handoffMs=warm_ms, replySeconds=secs)
        time.sleep(1)
        x_shot('05-desktop-warm-reply')

        # ---- Adapter-level checks against the running Desktop ---------------------------
        exact_at = time.time()
        res = run_adapter(EXACT_TEXT)
        if res.get('status') != 'accepted':
            fail('exact: untrimmed, slash-leading text accepted', result=res)
        accepted = accepted_lines(log_offset, 3)
        exact_sid = accepted[-1].split()[-1]
        detail, secs = wait_reply(exact_sid, 0, EXACT_REPLY, seconds=120)
        if new_desktop_session_ids(exact_at - 1) != [exact_sid] or not detail or detail['users'] != [EXACT_TEXT]:
            fail('exact: user turn is byte-identical (leading spaces, "/help", trailing newline); not run as a slash command', session=detail)
        evidence['exactSession'] = {**detail, 'textSha256': sha(EXACT_TEXT.encode())}
        note('exact: Desktop stored the untrimmed slash-leading text byte-for-byte as a user turn (no slash command ran)',
             replyMatched=bool(secs))

        before = new_desktop_session_ids(run_started - 1)
        res_rej = run_adapter('QA text \x1b[200~with a bracketed-paste marker')
        res_empty = run_adapter('   ')
        rid_dup, ack_dup = raw_request({'requestId': cold_rid})
        rid_exp, ack_exp = raw_request({'expiresAt': int(time.time() * 1000) - 1000})
        time.sleep(3)
        after = new_desktop_session_ids(run_started - 1)
        checks = {
            'sanitizerAlteredTextRejected': res_rej.get('status') == 'error' and res_rej.get('error') == 'rejected',
            'blankRejectedBeforeDesktop': res_empty == {'status': 'error', 'error': 'input'},
            'replayedRequestIdRejected': ack_dup.get('status') == 'error' and ack_dup.get('error') == 'rejected' and ack_dup.get('requestId') == cold_rid,
            'expiredRequestRejected': ack_exp.get('status') == 'error' and ack_exp.get('error') == 'rejected',
            'noNewConversation': before == after,
            'acksAreMarked': all(a.get('handler') == 'hermes-desktop-launcher-handoff' and 'text' not in a for a in (ack_dup, ack_exp)),
            'samePrimary': desktop_primary() == [primary],
        }
        evidence['refusals'] = {'rejected': res_rej, 'blank': res_empty, 'replay': ack_dup, 'expired': ack_exp, 'checks': checks}
        if not all(checks.values()):
            fail('failure paths: rejected/blank/replay/expired create nothing', checks=checks)
        note('failure paths: sanitizer-altered text rejected, blank refused before Desktop, replayed requestId and expired request rejected; no conversation created')

        # ---- Exactly once across the run -------------------------------------------------
        all_new = new_desktop_session_ids(run_started - 1)
        if sorted(all_new) != sorted([cold_sid, warm_sid, exact_sid]):
            fail('exactly once: three accepted handoffs, three conversations', newSessions=len(all_new))
        note('exactly once: 3 accepted handoffs -> 3 new conversations, each with its one handoff turn')

        # ---- Clipboard, requests, collectors, git ----------------------------------------
        calls = (qa / 'calls.log').read_text().splitlines() if (qa / 'calls.log').exists() else []
        evidence['collectorCalls'] = {'wl-paste': sum(c.startswith('wl-paste') for c in calls), 'wl-copy': sum(c.startswith('wl-copy') for c in calls),
                                      'hyprctl': sorted({c for c in calls if c.startswith('hyprctl')})}
        evidence['gitRefused'] = (qa / 'git.log').read_text().splitlines() if (qa / 'git.log').exists() else []
        if evidence['collectorCalls']['wl-copy']:
            fail('nothing copied to the clipboard')
        note('clipboard never written; sentinel absent from every handoff turn', gitRefused=evidence['gitRefused'][:5])
        reqs = js('return window.__hf.requests')
        bridge = [r for r in reqs if r['host'].endswith(':7331')]
        if not all(r.get('refused') for r in bridge):
            fail('soft-if bridge reached only the refusing hook', bridge=bridge)
        evidence['requestSummary'] = {'bridgeRefused': len(bridge), 'otherHosts': sorted({r['host'] for r in reqs if r['host'] != LAB and not r['host'].endswith(':7331')})}
        evidence['executions'] = agent_execs()
        evidence['handoffLog'] = handoff_log_lines(log_offset)
        evidence['passed'] = True
    except Exception as error:  # noqa: BLE001 - evidence records any failure
        evidence['error'] = str(error)[:400]
        try:
            evidence['executionsAtFailure'] = js('return window.__hf.executions')
            evidence['handoffLogAtFailure'] = handoff_log_lines(log_offset)
        except Exception:  # noqa: BLE001
            pass
        try:
            x_shot('99-failure')
        except Exception:  # noqa: BLE001
            pass
    finally:
        if session:
            try:
                request(base, 'DELETE', f'/session/{session}', timeout=3)
            except Exception:  # noqa: BLE001
                pass
        for proc in (driver,):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        # Desktop is this run's own private instance: quit it so its backend shuts down cleanly.
        for p in desktop_primary():
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for _ in range(150):
            if not desktop_procs():
                break
            time.sleep(.1)
        time.sleep(1)
        stray = [p for p in marked_pids(args.marker) if p != os.getppid()
                 and Path(proc_argv(p)[0] if proc_argv(p) else '').name not in ('dbus-daemon', 'dbus-run-session')]
        for p in stray:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(.5)
        evidence['innerCleanup'] = {'strayTerminated': [{'pid': p, 'argv0': Path(proc_argv(p)[0]).name if proc_argv(p) else '?'} for p in stray],
                                    'requestDirsLeft': [p.name for p in rt.glob('slhd-*')]}
        evidence['screenshots'] = shots
        args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'passed': evidence['passed'], 'checks': len(evidence['checks']), 'error': evidence.get('error'),
                      'failure': evidence.get('failure')}, indent=2, ensure_ascii=False)[:3000])
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
