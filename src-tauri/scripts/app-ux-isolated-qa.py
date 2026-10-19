#!/usr/bin/env python3
"""Installed-app cards on the real native binary, inside a private X11 display.

Drives the Tauri WebKit UI through WebKitWebDriver, against the real native app
index and icons. Nothing is launched and nothing reaches the model:

- `open_app` execute IPC is intercepted and answered in the page, so the exact
  dispatch payload is recorded without starting an application (Brave's singleton
  would otherwise open a window on the host session).
- Decision requests to the local model are refused in the page, which also checks
  that apps stay usable while the model is offline.

Input stays in the launcher DOM; no global keys. Only public application names
and desktop IDs from the representative list are recorded, plus counts.
Run only through run-app-ux-isolated-qa.py.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / 'src-tauri/visual-qa/app-ux-isolated'
# (query, expected first card name or None, expected other names that must be selectable)
QUERIES = [
    ('br', 'Brave', ['Broken Arrow']),
    ('brave browser', 'Brave', []),
    ('brave', 'Brave', []),
    ('firefox', 'Firefox', []),
    ('fire', 'Firefox', []),
    ('chromium', 'Chromium', []),
    ('visual studio code', 'Visual Studio Code', []),
    ('vscode', 'Visual Studio Code', []),
    ('foot', 'Foot', []),
    ('dolphin', 'Dolphin', []),
    ('discord', 'Discord', []),
    ('dis', None, ['Discord']),
    ('broken arrow', 'Broken Arrow', []),
    ('bro', None, ['Broken Arrow']),
    ('steam', 'Steam', []),
    ('obs studio', 'OBS Studio', []),
    ('obs', 'OBS Studio', []),
    ('code', None, ['Visual Studio Code']),
    ('web browser', None, ['Brave', 'Firefox', 'Chromium']),
]


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


def launcher_pids():
    result = set()
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal():
            try:
                if (entry / 'exe').resolve(strict=True).name == 'soft-launcher':
                    result.add(int(entry.name))
            except (OSError, RuntimeError):
                pass
    return result


HOOK = r"""
window.__appQa={executions:[],hides:0,apps:null,modelBlocked:0,failNext:false,states:0};
const originalFetch=window.__appQaFetch??window.fetch;
window.__appQaFetch=originalFetch;
const json=(value,ok=true)=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json','Tauri-Response':ok?'ok':'error'}});
window.fetch=async function(...args){
 const target=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
 const ipc=target.protocol==='ipc:'||target.hostname==='ipc.localhost';
 if(!ipc&&target.port==='11434'){window.__appQa.modelBlocked++;throw new TypeError('Failed to fetch (model blocked by isolated QA)');}
 if(ipc&&target.pathname==='/execute'){
  const payload=JSON.parse(args[1].body);
  const r=payload?.request??{};
  if(r.actionId!=='open_app')return json('Isolated QA refuses non-app execution',false);
  window.__appQa.executions.push({actionId:r.actionId,appId:r.args?.appId,keys:Object.keys(r.args??{}).sort(),at:performance.now()});
  if(window.__appQa.failNext){window.__appQa.failNext=false;return json('spawn failed (isolated QA)',false);}
  return json({status:'executed',actionId:'open_app',message:'Isolated QA intercepted the launch'});
 }
 if(ipc&&target.pathname==='/hide_window')window.__appQa.hides++;
 if(ipc&&target.pathname==='/get_state')window.__appQa.states++;
 const response=await originalFetch(...args);
 if(ipc&&target.pathname==='/get_apps'){
  try{const apps=await response.clone().json();window.__appQa.apps={count:apps.length,icons:apps.filter(a=>typeof a.icon==='string'&&a.icon.startsWith('data:image/png;base64,')).length,maxIconChars:Math.max(0,...apps.map(a=>a.icon?.length??0)),nonPngIcons:apps.filter(a=>a.icon&&!a.icon.startsWith('data:image/png;base64,')).length};}catch(e){}
 }
 return response;
};
return true;
"""

CARDS = """
return [...document.querySelectorAll('.app')].map(el=>({
  name:el.querySelector('.name')?.textContent.trim(),
  icon:!!el.querySelector('.icon img'),
  monogram:!!el.querySelector('.monogram'),
  target:el.classList.contains('target'),
  cue:el.querySelector('.cue')?.textContent.trim()??null,
  label:el.getAttribute('title'),
  clipped:(()=>{const n=el.querySelector('.name');return !!n&&(n.scrollHeight>n.clientHeight+1||n.scrollWidth>n.clientWidth+1)})(),
  key:el.id.replace('launcher-card-','')}));
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'src-tauri/app-ux-isolated-qa.json')
    parser.add_argument('--isolated-display', action='store_true')
    parser.add_argument('--profile-dir', type=Path, required=True, help='Empty private directory for the WebKit/XDG profile')
    args = parser.parse_args()
    if not args.isolated_display or os.environ.get('SOFT_LAUNCHER_ISOLATED_QA') != '1' or os.environ.get('WAYLAND_DISPLAY'):
        parser.error('Refusing GUI launch outside the provisioned isolated X11 display')
    binary = args.binary.resolve(strict=True)
    args.profile_dir = args.profile_dir.resolve()
    if args.profile_dir.exists() and any(args.profile_dir.iterdir()):
        parser.error('--profile-dir must be empty or absent')
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    host_before = launcher_pids()
    evidence = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'display': os.environ.get('DISPLAY'), 'gdkBackend': os.environ.get('GDK_BACKEND'),
                'hostWaylandUnset': 'WAYLAND_DISPLAY' not in os.environ,
                'privateDbus': bool(os.environ.get('DBUS_SESSION_BUS_ADDRESS')),
                'hostLaunchersBefore': len(host_before), 'appLaunchesIntercepted': True,
                'modelRequestsRefused': True, 'queries': [], 'checks': [], 'passed': False}
    driver = session = native_pid = None
    base = ''
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = os.environ.copy()
        env['TAURI_WEBVIEW_AUTOMATION'] = 'true'
        env['SOFT_LAUNCHER_CATALOGUE'] = str(ROOT / 'catalogue.toml')
        # A private WebKit profile (settings, notice ledger) so the user's launcher
        # profile is untouched; the user's own desktop entries stay indexed.
        home = Path.home()
        env['XDG_DATA_HOME'] = str(args.profile_dir / 'data')
        env['XDG_CONFIG_HOME'] = str(args.profile_dir / 'config')
        env['XDG_CACHE_HOME'] = str(args.profile_dir / 'cache')
        env['XDG_DATA_DIRS'] = ':'.join([str(home / '.local/share'), str(home / '.local/share/flatpak/exports/share'),
                                         '/var/lib/flatpak/exports/share', os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share')])
        driver = subprocess.Popen(['WebKitWebDriver', '--host=127.0.0.1', f'--port={port}'], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                request(base, 'GET', '/status', timeout=1)
                break
            except (OSError, RuntimeError):
                time.sleep(.05)
        created = request(base, 'POST', '/session', {'capabilities': {'alwaysMatch': {'webkitgtk:browserOptions': {'binary': str(binary), 'args': ['--hidden']}}}})
        session = created['sessionId']
        native_pid = created['capabilities'].get('webkit:processID')
        assert native_pid not in host_before, 'Session attached to an existing launcher'

        def js(script):
            return request(base, 'POST', f'/session/{session}/execute/sync', {'script': script, 'args': []})

        def wait(script, label, seconds=15):
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                value = js(script)
                if value:
                    evidence['checks'].append({'check': label, 'ms': round((time.monotonic() - start) * 1000)})
                    return value
                time.sleep(.06)
            evidence['failure'] = {'check': label, 'cards': js(CARDS), 'hint': js("return document.querySelector('.feedback .line, .feedback .box')?.innerText?.slice(0,200)??null")}
            raise AssertionError(label)

        def check(ok, label):
            if not ok:
                evidence['failure'] = {'check': label, 'cards': js(CARDS)}
                raise AssertionError(label)
            evidence['checks'].append({'check': label})

        def type_text(value):
            js("const i=document.querySelector('#launcher-input');i.focus();i.value=" + json.dumps(value) + ";i.dispatchEvent(new Event('input',{bubbles:true}));return true")
            time.sleep(.15)

        def key(name, repeat=False, up=True):
            js(f"window.dispatchEvent(new KeyboardEvent('keydown',{{key:{json.dumps(name)},bubbles:true,repeat:{'true' if repeat else 'false'}}}));" + (f"window.dispatchEvent(new KeyboardEvent('keyup',{{key:{json.dumps(name)},bubbles:true}}));" if up else '') + 'return true')
            time.sleep(.08)

        def shot(name):
            png = base64.b64decode(request(base, 'GET', f'/session/{session}/screenshot'))
            (SHOTS / f'{name}.png').write_bytes(png)
            evidence.setdefault('screenshots', []).append(str((SHOTS / f'{name}.png').relative_to(ROOT)))

        def reopen():
            wait('return window.__appQa.hides>0', 'launcher hid after the launch')
            js('window.__appQa.hides=0;return true')
            subprocess.run([str(binary), '--toggle'], cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            time.sleep(.4)

        wait("return !!window.__TAURI_INTERNALS__&&!!document.querySelector('#launcher-input')", 'real Tauri webview mounted (hidden start)')
        evidence['origin'] = js('return location.origin')
        assert evidence['origin'] not in ('http://127.0.0.1:5173', 'http://localhost:5173'), 'Expected embedded production assets'
        # Started with --hidden: nothing has called get_apps or the model yet. Hook first, then show.
        js(HOOK)
        subprocess.run([str(binary), '--toggle'], cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        wait('return window.__appQa.apps!==null', 'native get_apps answered after the first show')
        evidence['index'] = js('return window.__appQa.apps')
        check(evidence['index']['nonPngIcons'] == 0 and evidence['index']['maxIconChars'] < 24 * 1024 * 4 // 3 + 64, 'icons are bounded PNG data URLs only')
        type_text('br')
        wait("return document.querySelectorAll('.app').length>0", 'app cards appear for "br" with the model offline')

        for query, first, selectable in QUERIES:
            type_text(query)
            cards = wait(CARDS.replace('return [', 'const c=[').replace("}));", "}));return c.length?c:null;"), f'cards for "{query}"', seconds=4)
            names = [c['name'] for c in cards]
            row = {'query': query, 'names': names, 'first': names[0], 'targetIsFirst': cards[0]['target'],
                   'clipped': [c['name'] for c in cards if c['clipped']],
                   'icons': [c['icon'] for c in cards], 'enterCue': cards[0]['cue']}
            evidence['queries'].append(row)
            if first:
                check(names[0] == first, f'"{query}" leads with {first}')
            for name in selectable:
                check(name in names, f'"{query}" offers {name} as a selectable card')
            for name in [first, *selectable]:
                if name in names:
                    check(not next(c for c in cards if c['name'] == name)['clipped'], f'"{query}" shows the full name {name}')
            check(cards[0]['target'] and cards[0]['cue'] == '↵ open', f'"{query}" first card is the Enter target with the open cue')
            check(sum(c['target'] for c in cards) == 1, f'"{query}" has exactly one target')
            check(len({c['label'] for c in cards}) == len(cards), f'"{query}" cards are distinguishable (name and subtitle)')
            if query in ('br', 'brave browser', 'vscode', 'obs', 'steam', 'visual studio code', 'web browser'):
                shot('query-' + query.replace(' ', '-'))

        # Model offline: no model card row noise, apps still the answer.
        check(js('return window.__appQa.modelBlocked') > 0, 'decision requests were refused (offline model)')

        # One Enter press opens the default app; repeat and release do nothing more.
        type_text('br')
        wait("return document.querySelector('.app.target .name')?.textContent==='Brave'", 'Brave is the default target for "br"')
        js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));window.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,repeat:true}));return true")
        time.sleep(.05)
        key('Enter', up=True)
        wait("return window.__appQa.executions.length>=1", 'one Enter dispatched open_app')
        time.sleep(.5)
        execs = js('return window.__appQa.executions')
        check(len(execs) == 1 and execs[0]['appId'] == 'brave-browser.desktop' and execs[0]['keys'] == ['appId'], 'exactly one open_app {appId: brave-browser.desktop}; no hold, no repeat')
        reopen()

        # Tab selection is stable and opens the selected app with one Enter.
        type_text('bro')
        wait("return document.querySelectorAll('.app').length>1", 'several apps for "bro"')
        names = js("return [...document.querySelectorAll('.app .name')].map(n=>n.textContent)")
        steps = names.index('Broken Arrow')
        for _ in range(steps):
            key('Tab')
        wait("return document.querySelector('.app.target .name')?.textContent==='Broken Arrow'", 'Tab selects Broken Arrow')
        time.sleep(1.5)
        check(js("return document.querySelector('.app.target .name')?.textContent") == 'Broken Arrow', 'selection stays on Broken Arrow while async results settle')
        shot('tab-selected-bro')
        key('Enter')
        wait("return window.__appQa.executions.length>=2", 'Enter on the selected app dispatched')
        check(js('return window.__appQa.executions[1].appId') == 'net.lutris.broken-arrow-22.desktop', 'selected app opened, not the default one')
        reopen()

        # Arrow keys reach the same cards; Arrow Up returns to the app row.
        type_text('web browser')
        wait("return document.querySelectorAll('.app').length>=3", 'browsers for "web browser"')
        key('Tab')
        key('Tab')
        third = js("return document.querySelector('.app.target .name')?.textContent")
        key('ArrowUp')
        check(js("return document.querySelector('.app.target .name')?.textContent") in (third, js("return document.querySelector('.app .name')?.textContent")), 'Arrow Up keeps the target in the app row')

        # A click released on the card opens it.
        type_text('firefox')
        wait("return document.querySelector('.app.target .name')?.textContent==='Firefox'", 'Firefox card for click')
        js("const c=document.querySelector('.app');const r=c.getBoundingClientRect();const o={bubbles:true,button:0,pointerId:1,clientX:r.left+r.width/2,clientY:r.top+r.height/2};c.dispatchEvent(new PointerEvent('pointerdown',o));c.dispatchEvent(new PointerEvent('pointerup',o));return true")
        wait("return window.__appQa.executions.length>=3", 'click dispatched open_app')
        check(js('return window.__appQa.executions[2].appId') == 'firefox.desktop', 'click opened Firefox')
        reopen()

        # A failed launch stays open with the card restored; one more Enter retries.
        type_text('brave browser')
        wait("return document.querySelector('.app.target .name')?.textContent==='Brave'", 'Brave card before failure')
        js('window.__appQa.failNext=true;return true')
        key('Enter')
        wait("return document.body.innerText.includes('Open Brave failed')", 'failure explained in the outcome panel')
        wait("return document.querySelector('.app.target .name')?.textContent==='Brave'", 'Brave card restored after the failure')
        check(js('return window.__appQa.hides') == 0, 'launcher stayed open after the failure')
        shot('failed-launch-recovery')
        key('Escape')  # dismiss the outcome panel
        key('Enter')
        wait("return window.__appQa.executions.length>=5", 'retry dispatched after the failure')
        reopen()

        # Escape dismisses without dispatching anything.
        before = js('return window.__appQa.executions.length')
        type_text('br')
        key('Escape')
        wait('return window.__appQa.hides>0', 'Escape hides the launcher')
        check(js('return window.__appQa.executions.length') == before, 'Escape dispatched nothing')

        host_after = launcher_pids() - {native_pid}
        evidence['hostLaunchersAfter'] = len(host_after)
        check(host_before <= host_after | host_before and host_before == (host_after & host_before), 'existing host launcher processes untouched')
        evidence['executions'] = js('return window.__appQa.executions.map(e=>({appId:e.appId,keys:e.keys}))')
        evidence['passed'] = True
    except Exception as error:  # noqa: BLE001 - evidence records any failure
        evidence['error'] = str(error)
    finally:
        args.output.write_text(json.dumps(evidence, indent=2) + '\n')
        if driver:
            if session:
                try:
                    request(base, 'DELETE', f'/session/{session}', timeout=3)
                except Exception:  # noqa: BLE001
                    pass
            if native_pid:
                try:
                    os.kill(native_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            driver.terminate()
            try:
                driver.wait(timeout=3)
            except subprocess.TimeoutExpired:
                driver.kill()
    print(json.dumps({'passed': evidence['passed'], 'checks': len(evidence['checks']), 'error': evidence.get('error'), 'output': str(args.output.relative_to(ROOT))}, indent=2))
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
