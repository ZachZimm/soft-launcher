#!/usr/bin/env python3
"""Bare-domain Open URL UI QA for a launcher binary, inside the hidden agent workspace only.

Started by run-url-ui-isolated-qa.py. Same isolation as routing-ui-isolated-qa.py:
private Xvfb display, private HOME/XDG profile and dbus-run-session bus, launcher
under WebKitWebDriver, keys and pointer as DOM events in its own page.

Nothing opens. The in-page IPC spy answers `execute` itself: an Open URL request
gets a synthetic `executed` reply after its exact arguments are recorded, every
other blocked command a synthetic refusal. Behind the spy, xdg-open, gio and the
browsers on PATH are refusing logging stand-ins. soft-if logging is refused in the
page after the runtime is rebuilt behind the hook while hidden, before the window
is ever shown or any input typed.

Checks: the native header has no LIVE/mode badge and the input spans the row;
`openrouter.ai` typed per character gets a valid Open URL card (no "can't run"
or "Enter an http or https URL"), and Enter sends exactly one execute with
{url: "https://openrouter.ai/"}; explicit `https://openrouter.ai/` likewise;
malformed/dangerous inputs never reach execute even when the Open URL card is
targeted and Enter is held. Policy, bands, hold and confirmation are untouched.
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
import shutil
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
LAB = os.environ.get('SOFT_LAUNCHER_QA_CLEF_HOST', '127.0.0.1:11434')
WINDOW_CLASS = 'qa-public-editor'
WINDOW_TITLE = 'QA-TITLE-7d2e Please set up the transcription server on lab and install a mod manager'
CLIP_TEXT = 'QA-CLIP-91b4 Install and configure the deadlock mod manager for me, then check the lab transcription server'
SENTINELS = ('QA-TITLE-7d2e', 'QA-CLIP-91b4', 'Please set up the transcription', 'Install and configure the deadlock')

# (label, input, typed per character, expected execute url)
VALID = [
    ('bare-domain', 'openrouter.ai', True, 'https://openrouter.ai/'),
    ('explicit-https', 'https://openrouter.ai/', False, 'https://openrouter.ai/'),
]
# Malformed or dangerous: must never reach execute, even with the Open URL card targeted and Enter held.
BLOCKED = [
    ('javascript-scheme', 'javascript:alert(1)'),
    ('credentials', 'https://user:pass@openrouter.ai/'),
    ('missing-slash', 'http:/openrouter.ai'),
]
OLD_ERROR = 'Enter an http or https URL'
SPY_OPENED = 'Opened (isolated URL QA spy; native not invoked)'
SPY_REFUSAL = 'Refused by isolated URL QA spy (native not invoked)'
STAND_INS = ('hermes', 'xdg-open', 'gio', 'firefox', 'chromium', 'brave', 'google-chrome-stable', 'ssh', 'foot', 'kitty',
             'ghostty', 'alacritty', 'wezterm', 'loginctl', 'hyprlock', 'swaylock', 'grim', 'slurp', 'nmcli', 'rfkill',
             'wpctl', 'pactl', 'playerctl', 'code', 'notify-send', 'systemctl', 'pkill', 'kill')


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
    return {k.decode(errors='replace'): v.decode(errors='replace') for k, _, v in (i.partition(b'=') for i in raw.split(b'\0')) if k}


def proc_argv(pid):
    try:
        return [a.decode(errors='replace') for a in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if a]
    except OSError:
        return []


def proc_exe(pid):
    try:
        return os.readlink(f'/proc/{pid}/exe')
    except OSError:
        return None


def marked_pids(marker):
    return sorted(int(e.name) for e in Path('/proc').iterdir()
                  if e.name.isdecimal() and int(e.name) != os.getpid() and proc_env(int(e.name)).get('SL_URL_QA_RUN') == marker)


def write_exec(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o700)


def make_fakes(qa):
    """Public context collectors and refusing action stand-ins; every call is logged."""
    bin_dir = qa / 'bin'
    bin_dir.mkdir()
    (qa / 'clipboard.txt').write_text(CLIP_TEXT)
    window = json.dumps({'class': WINDOW_CLASS, 'title': WINDOW_TITLE, 'address': '0x51a', 'pid': 1})
    (qa / 'window.json').write_text(window)
    write_exec(bin_dir / 'hyprctl', f"""#!/bin/sh
echo "hyprctl $*" >> '{qa}/calls.log'
if [ "$1" = activewindow ]; then cat '{qa}/window.json'; exit 0; fi
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
    for name in STAND_INS:
        write_exec(bin_dir / name, f"""#!/bin/sh
echo "action-stand-in {name}" >> '{qa}/actions.log'
exit 1
""")
    adapter = qa / 'spy-hermes-adapter.py'
    write_exec(adapter, f"""#!/usr/bin/env python3
# Routing QA stand-in for the Hermes adapter: records a call and refuses.
open({str(qa / 'actions.log')!r}, 'a').write('action-stand-in hermes-adapter\\n')
raise SystemExit(1)
""")
    return bin_dir, adapter


HOOK = r"""
const qa=window.__uq??{requests:[],decisions:[],ipc:[],blocked:[],postMessage:[],hides:0};window.__uq=qa;
const SENT=%SENTINELS%;const CLASS=%CLASS%;const REFUSAL=%REFUSAL%;
const BLOCK=new Set(['execute','propose_action','save_proposal','reload','qa_spy_probe']);
const reply=(ok,body)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json','Tauri-Response':ok?'ok':'error'}});
const originalFetch=window.__uqFetch??window.fetch;window.__uqFetch=originalFetch;
window.fetch=async function(...args){
 const target=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
 const ipc=target.protocol==='ipc:'||target.hostname==='ipc.localhost';
 if(!ipc){
  const row={host:target.host,path:target.pathname};qa.requests.push(row);
  if(target.port==='7331'||(target.port==='11434'&&target.host!=="%CLEF_HOST%")){row.refused=true;throw new TypeError('Failed to fetch (refused by isolated QA)');}
  if(target.host==="%CLEF_HOST%"){
   const body=typeof args[1]?.body==='string'?args[1].body:'';
   const d={at:Date.now(),bytes:body.length,sentinels:SENT.filter(s=>body.includes(s)),hasWindowClass:body.includes(CLASS),
    hasClipboardKind:/clipboard[^a-z]{0,8}kind/i.test(body),hasTitleKey:/\\?"title\\?"\s*:/.test(body),hasHeadKey:/\\?"head\\?"\s*:/.test(body),
    hasInstalledAppMatch:body.includes('installedAppMatch')};
   d.input=qa.expect??null;d.hasExpectedInput=typeof qa.expect==='string'&&body.includes(JSON.stringify(JSON.stringify(qa.expect)).slice(3,-3));
   qa.decisions.push(d);
   try{const r=await originalFetch(...args);d.status=r.status;
    try{const j=JSON.parse(await r.clone().text());const find=o=>{if(o&&typeof o==='object'){if(typeof o.send_to_agent==='number'&&typeof o.web_search==='number')return o;for(const v of Object.values(o)){const f=find(v);if(f)return f;}}return null;};
     const pr=find(j);if(pr)d.top=Object.entries(pr).filter(e=>typeof e[1]==='number').sort((a,b)=>b[1]-a[1]).slice(0,5).map(([k,v])=>[k,Math.round(v*1e4)/1e4]);}catch(e){d.topError=String(e).slice(0,80);}
    return r;}catch(e){d.error=String(e).slice(0,80);throw e;}
  }
  try{const r=await originalFetch(...args);row.status=r.status;return r;}catch(e){row.error=String(e).slice(0,80);throw e;}
 }
 const cmd=decodeURIComponent(target.pathname.replace(/^\//,''));
 qa.ipc.push(cmd);
 if(cmd==='hide_window')qa.hides++;
 if(BLOCK.has(cmd)){
  let payload={};try{payload=JSON.parse(args[1]?.body??'{}');}catch(e){}
  const r=payload.request??{};
  const input=r.args?.input;
  const row={cmd,actionId:r.actionId??null,keys:Object.keys(r.args??{}).sort(),url:typeof r.args?.url==='string'?r.args.url:null,token:!!r.confirmationToken,at:Date.now(),inputLength:typeof input==='string'?input.length:null,inputSha256:null};
  if(typeof input==='string'){const dg=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(input));row.inputSha256=[...new Uint8Array(dg)].map(b=>b.toString(16).padStart(2,'0')).join('');}
  qa.blocked.push(row);
  if(cmd==='execute'&&r.actionId==='open_url'&&!r.confirmationToken){row.answer='synthetic executed';return reply(true,{status:'executed',actionId:'open_url',message:%OPENED%});}
  row.answer='synthetic refusal';
  return reply(false,REFUSAL);
 }
 return originalFetch(...args);
};
if(!window.__uqES){window.__uqES=window.EventSource;window.EventSource=function(url,...rest){const t=new URL(url,location.href);qa.requests.push({host:t.host,path:t.pathname,refused:t.port==='7331',sse:true});if(t.port==='7331')throw new TypeError('EventSource refused by isolated QA');return new window.__uqES(url,...rest);};}
try{if(window.ipc&&!window.__uqPM){const pm=window.ipc.postMessage.bind(window.ipc);window.__uqPM=pm;window.ipc.postMessage=function(data){let cmd='?';try{cmd=JSON.parse(data).cmd;}catch(e){}qa.postMessage.push(cmd);if(BLOCK.has(cmd))return;return pm(data);};qa.postMessageWrapped=true;}}catch(e){qa.postMessageWrapped=false;}
return true;
"""
CARDS = r"""
return [...document.querySelectorAll('#launcher-row .card')].map(c=>({id:c.id.replace('launcher-card-',''),leader:c.classList.contains('leader'),
 primed:c.classList.contains('primed'),target:c.classList.contains('target'),label:c.getAttribute('aria-label'),
 num:c.querySelector('.num')?.textContent?.trim()??null,confirmTag:!!c.querySelector('.tag.warn'),
 detail:c.querySelector('.detail')?.textContent?.trim()??'',error:c.querySelector('.detail.error')?.textContent?.trim()??null,cue:c.querySelector('.cue')?.textContent?.trim()??null,x:Number((c.style.transform.match(/translateX\(([-\d.]+)px/)||[])[1]??NaN),
 width:c.getBoundingClientRect().width}));
"""
APPS = r"""return [...document.querySelectorAll('#launcher-apps .app')].map(a=>({id:a.id,label:a.getAttribute('aria-label'),target:a.classList.contains('target')}))"""
STATUS = "return document.querySelector('.state[role=status]')?.textContent?.trim()??''"
FEEDBACK = "return [...document.querySelectorAll('.feedback,.error')].map(e=>e.innerText.trim()).filter(Boolean).join(' | ').slice(0,300)"


def parse_num(text):
    """'34% · c 34%' -> (0.34, 0.34); '12%' -> (0.12, None)."""
    if not text:
        return None, None
    parts = [p.strip() for p in text.split('·')]
    def pct(s):
        s = s.replace('c ', '').replace('%', '').replace('<', '').strip()
        try:
            return float(s) / 100
        except ValueError:
            return None
    return pct(parts[0]), (pct(parts[1]) if len(parts) > 1 else None)


HEADER = r"""
const row=document.querySelector('.input-row');const i=document.querySelector('#launcher-input');const rr=row.getBoundingClientRect();const ir=i.getBoundingClientRect();
return {mode:document.documentElement.dataset.mode,badges:row.querySelectorAll('.mode').length,rowText:row.innerText.trim(),liveTextAnywhere:/\bLIVE\b/.test(document.body.innerText),
 rowWidth:Math.round(rr.width),inputWidth:Math.round(ir.width),inputLeftGap:Math.round(ir.left-rr.left),inputRightGap:Math.round(rr.right-ir.right),rowHeight:Math.round(rr.height),inputHeight:Math.round(ir.height)};
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('candidate', 'installed'), required=True)
    parser.add_argument('--display', required=True)
    parser.add_argument('--workspace-runtime', type=Path, required=True)
    parser.add_argument('--profile-dir', type=Path, required=True)
    parser.add_argument('--marker', required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--catalogue', type=Path, required=True)
    parser.add_argument('--shots', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    ws_rt = args.workspace_runtime.resolve(strict=True)
    qa = args.profile_dir.resolve()
    home = qa / 'home'
    refusals = []
    if os.environ.get('SL_URL_QA') != '1' or os.environ.get('SL_URL_QA_RUN') != args.marker:
        refusals.append('not started by the isolated runner')
    for name in ('WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE', 'SWAYSOCK', 'WAYLAND_SOCKET', 'SSH_AUTH_SOCK'):
        if os.environ.get(name):
            refusals.append(f'{name} inherited from the host')
    if os.environ.get('DISPLAY') != args.display or not args.display.startswith(':') or args.display in (':0', ':1'):
        refusals.append('DISPLAY is not the workspace display')
    if ws_rt.parent != Path(f'/run/user/{os.getuid()}/agent-workspace-linux') or qa.parent != ws_rt:
        refusals.append('profile is not inside the workspace runtime')
    if Path(os.environ.get('XDG_RUNTIME_DIR', '/')).resolve() != qa / 'rt':
        refusals.append('XDG_RUNTIME_DIR is not private')
    if Path(os.environ.get('HOME', '/')).resolve() != home:
        refusals.append('HOME is not the private QA home')
    bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
    if not bus.startswith('unix:path=/tmp/dbus-') or f'/run/user/{os.getuid()}/bus' in bus:
        refusals.append('session bus is not the private dbus-run-session bus')
    if refusals:
        print(json.dumps({'passed': False, 'refused': refusals}))
        return 2

    shots_dir = args.shots
    shots_dir.mkdir(parents=True, exist_ok=True)
    for old in shots_dir.glob('*.png'):
        old.unlink()
    catalogue = qa / 'catalogue.toml'
    shutil.copyfile(args.catalogue, catalogue)
    bin_dir, spy_adapter = make_fakes(qa)
    evidence = {
        'scope': args.stage, 'passed': False, 'binarySha256': sha(args.binary.read_bytes()),
        'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'stage': args.stage, 'binary': str(args.binary),
        'catalogueSource': str(args.catalogue), 'catalogueCopySha256': sha(catalogue.read_bytes()),
        'isolation': {'display': args.display, 'home': str(home), 'privateRuntime': os.environ['XDG_RUNTIME_DIR'], 'privateBus': bus.split(',')[0],
                      'xdgDataDirs': '/usr/local/share:/usr/share', 'hermesAdapter': 'logging stand-in (refuses)',
                      'nativeExecute': 'answered by the in-page spy; xdg-open/gio/browsers on PATH are refusing stand-ins'},
        'ambientFixture': {'windowClass': WINDOW_CLASS, 'windowTitle': WINDOW_TITLE, 'clipboard': CLIP_TEXT},
        'checks': [], 'header': None, 'cases': {},
    }
    driver = session = None
    base = ''
    shots = []

    def note(label, **extra):
        evidence['checks'].append({'check': label, **extra})

    def fail(label, **extra):
        evidence['failure'] = {'check': label, **extra}
        raise AssertionError(label)

    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = dict(os.environ)
        env.update({
            'TAURI_WEBVIEW_AUTOMATION': 'true', 'GDK_BACKEND': 'x11',
            'SOFT_LAUNCHER_CATALOGUE': str(catalogue),
            'SOFT_LAUNCHER_HERMES_ADAPTER': str(spy_adapter),
            'SOFT_LAUNCHER_CODEX_SSH_HOST': 'qa-refused.invalid', 'SOFT_LAUNCHER_CODEX_MODEL': 'gpt-6.1-sol',
            'SOFT_LAUNCHER_CLEF_URL': f'http://{LAB}',
            'PATH': f"{bin_dir}:{os.environ['PATH']}",
            'XDG_DATA_DIRS': '/usr/local/share:/usr/share',
        })
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
        if not native_pid or proc_env(native_pid).get('SL_URL_QA_RUN') != args.marker:
            fail("launcher process is this run's own instance")
        note('launcher binary started privately under WebDriver (--hidden)', exe=proc_exe(native_pid))

        def js(script):
            return request(base, 'POST', f'/session/{session}/execute/sync', {'script': script, 'args': []}, timeout=70)

        def wait(script, label, seconds=15, record=True):
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                if js(script):
                    if record:
                        note(label, ms=round((time.monotonic() - start) * 1000))
                    return True
                time.sleep(.05)
            fail(label, status=js(STATUS), feedback=js(FEEDBACK))

        def key(name, hold_ms=0):
            js(f"window.dispatchEvent(new KeyboardEvent('keydown',{{key:{json.dumps(name)},bubbles:true}}));return true")
            time.sleep(max(hold_ms, 40) / 1000)
            js(f"window.dispatchEvent(new KeyboardEvent('keyup',{{key:{json.dumps(name)},bubbles:true}}));return true")
            time.sleep(.08)

        def wd_shot(name):
            png = base64.b64decode(request(base, 'GET', f'/session/{session}/screenshot'))
            (shots_dir / f'{name}.png').write_bytes(png)
            shots.append(str((shots_dir / f'{name}.png').relative_to(ROOT)))

        def set_value(text):
            js("const i=document.querySelector('#launcher-input');i.focus();i.value=" + json.dumps(text) + ";i.dispatchEvent(new Event('input',{bubbles:true}));return true")

        def blocked():
            return js('return window.__uq.blocked')

        def show():
            subprocess.run([str(args.binary), '--toggle'], env={k: v for k, v in env.items() if k != 'TAURI_WEBVIEW_AUTOMATION'},
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            time.sleep(.4)

        shown = {'hides': 0}

        def ensure_shown():
            """Re-show after a successful open hid the overlay (hide_window is counted by the hook)."""
            hides = js('return window.__uq.hides')
            if hides > shown['hides']:
                show()
                shown['hides'] = hides
                wait("return document.querySelector('.state[role=status]')?.textContent?.trim()!=='warming'||false", 'shown again', seconds=60, record=False)

        def settle(text, label, typed):
            """Enter `text` (per character when `typed`), wait for a fresh ready decision and a stable row."""
            decisions_before = len(js('return window.__uq.decisions'))
            js('window.__uq.expect=' + json.dumps(text) + ';return true')
            set_value('')
            time.sleep(.3)
            if typed:
                for i in range(1, len(text) + 1):
                    set_value(text[:i])
                    time.sleep(.045)  # faster than the 80 ms debounce, like quick typing
            else:
                set_value(text)
            time.sleep(.15)
            wait(f"const s=document.querySelector('.state[role=status]')?.textContent?.trim();const i=document.querySelector('#launcher-input').value;"
                 f"return s==='ready'&&i==={json.dumps(text)}&&document.querySelectorAll('#launcher-row .card').length>0",
                 f'{label}: fresh ready decision for the exact input', seconds=20, record=False)
            samples = []
            for _ in range(12):  # 1.2 s: hysteresis settle ticks are 180 ms
                time.sleep(.1)
                samples.append([c['id'] for c in sorted(js(CARDS), key=lambda c: c['x'])][:3])
            decisions = js('return window.__uq.decisions')[decisions_before:]
            return {'cards': js(CARDS), 'stableTop3': all(s == samples[-1] for s in samples[-6:]), 'decisions': decisions,
                    'status': js(STATUS), 'bodyHasOldError': js(f"return document.body.innerText.includes({json.dumps(OLD_ERROR)})")}

        def target_card(card_id):
            js(f"const c=document.querySelector('#launcher-card-{card_id}');const r=c.getBoundingClientRect();const o={{bubbles:true,button:0,pointerId:1,clientX:r.left+r.width/2,clientY:r.top+r.height/2}};"
               "c.dispatchEvent(new PointerEvent('pointerdown',o));c.dispatchEvent(new PointerEvent('pointerup',o));return true")
            time.sleep(.15)

        def case_record(text, typed, result):
            cards = result['cards']
            ordered = sorted(cards, key=lambda c: c['x'])
            leader = next((c for c in cards if c['leader']), None)
            url_card = next((c for c in cards if c['id'] == 'open_url'), None)
            bodies = result['decisions']
            final_top = (bodies[-1].get('top') if bodies else None) or []
            p, conf = parse_num(leader['num']) if leader else (None, None)
            return {'input': text, 'typedPerCharacter': typed, 'modelTop': final_top[0][0] if final_top else None, 'modelProbabilities': final_top,
                    'leader': leader and leader['id'], 'leaderProbability': p, 'leaderConfidence': conf,
                    'leaderPrimed': bool(leader and leader['primed']), 'leaderNeedsConfirmation': bool(leader and leader['confirmTag']),
                    'openUrlCard': url_card and {k: url_card[k] for k in ('label', 'detail', 'error', 'cue', 'num', 'primed', 'target', 'confirmTag')},
                    'stableTop3': result['stableTop3'], 'top3': [c['id'] for c in ordered[:3]],
                    'cards': [{k: c[k] for k in ('id', 'num', 'primed', 'confirmTag', 'error')} for c in ordered[:6]],
                    'bodyHasOldError': result['bodyHasOldError'], 'decisionRequests': len(bodies),
                    'ambientInDecisionRequests': sorted({s for d in bodies for s in d['sentinels']}),
                    'decisionRequestShape': [{k: d.get(k) for k in ('hasExpectedInput', 'hasWindowClass', 'hasClipboardKind', 'hasTitleKey', 'hasHeadKey', 'status', 'bytes')} for d in bodies[-2:]]}

        def request_problems(case):
            shape = case['decisionRequestShape'][-1] if case['decisionRequestShape'] else {}
            problems = []
            if case['ambientInDecisionRequests']:
                problems.append('ambient title/clipboard text reached a decision request')
            if not shape.get('hasExpectedInput') or not shape.get('hasWindowClass') or shape.get('hasTitleKey') or shape.get('hasHeadKey'):
                problems.append('final decision request is not the class/kind-only projection of this input')
            return problems

        wait("return !!window.__TAURI_INTERNALS__&&!!document.querySelector('#launcher-input')", 'real Tauri webview mounted (hidden start)')
        origin = js('return location.origin')
        if origin in ('http://127.0.0.1:5173', 'http://localhost:5173'):
            fail('embedded production assets', origin=origin)
        js(HOOK.replace('"%CLEF_HOST%"', json.dumps(LAB)).replace('%SENTINELS%', json.dumps(list(SENTINELS))).replace('%CLASS%', json.dumps(WINDOW_CLASS))
           .replace('%REFUSAL%', json.dumps(SPY_REFUSAL)).replace('%OPENED%', json.dumps(SPY_OPENED)))
        probe = js("return window.__TAURI_INTERNALS__.invoke('qa_spy_probe',{}).then(()=>'resolved',e=>String(e))")
        if probe != SPY_REFUSAL or js('return window.__uq.blocked.length') != 1:
            fail('IPC spy answers blocked commands in the page (self-test)', probe=probe)
        note('IPC spy self-test: blocked command answered in the page, not by native', postMessageWrapped=js('return window.__uq.postMessageWrapped??null'))
        # soft-if's bridge captures fetch at mount; rebuild the runtime behind the hook while hidden,
        # before the window is shown or anything is typed, so no QA row can reach the host logger.
        js("window.dispatchEvent(new KeyboardEvent('keydown',{key:',',ctrlKey:true,bubbles:true}));return true")
        wait("return !!document.querySelector('.settings')", 'settings open while hidden (Ctrl+comma in page)')
        if not js("return document.querySelector('#s-numbers').checked"):
            js("document.querySelector('#s-numbers').click();return true")
        js("[...document.querySelectorAll('.presets button')].find(b=>b.textContent.includes('Clef on lab')).click();return true")
        time.sleep(.1)
        js('window.__uq.requests=[];return true')
        js("[...document.querySelectorAll('.settings button')].find(b=>b.textContent.includes('Apply and reconnect')).click();return true")
        time.sleep(.5)
        wait("return !!document.querySelector('#launcher-input')&&!document.querySelector('.settings')", 'runtime rebuilt behind the logging hook while hidden; probabilities shown on cards', seconds=10)
        if js('return window.__uq.decisions.length'):
            fail('no decision request before the window is shown')
        show()
        shown['hides'] = js('return window.__uq.hides')
        wait("return document.querySelector('.state[role=status]')?.textContent?.trim()!=='warming'||false", 'shown; warmup settled', seconds=120)
        note('embedded production assets; one private launcher', origin=origin)

        # ---- Header: no LIVE badge in the native overlay; the input takes the row ----
        header = js(HEADER)
        evidence['header'] = header
        if header['mode'] != 'native' or header['badges'] or 'LIVE' in header['rowText'] or header['liveTextAnywhere'] \
                or header['inputLeftGap'] > 1 or header['inputRightGap'] > 1:
            fail('native header has no LIVE badge and the input spans the row', header=header)
        wd_shot('header-empty')
        note(f"native header: no mode badge, no LIVE text; input {header['inputWidth']}px of {header['rowWidth']}px row")

        # ---- Valid URLs: valid Open URL card, Enter sends exactly one execute with the normalized url ----
        for label, text, typed, want_url in VALID:
            spy_before = len(blocked())
            hides_before = js('return window.__uq.hides')
            ensure_shown()
            result = settle(text, label, typed)
            case = case_record(text, typed, result)
            evidence['cases'][label] = case
            wd_shot(f'{label}-card')
            problems = request_problems(case)
            card = case['openUrlCard']
            if case['modelTop'] != 'open_url':
                problems.append(f"model top choice {case['modelTop']} != open_url")
            if case['leader'] != 'open_url':
                problems.append(f"leader card {case['leader']} != open_url")
            if not card or card['error'] or card['cue'] == 'can’t run' or result['bodyHasOldError']:
                problems.append('Open URL card shows an argument error / can’t run')
            if card and card['detail'] != 'openrouter.ai':
                problems.append(f"Open URL card detail {card and card['detail']!r} is not the normalized host")
            if case['leaderNeedsConfirmation']:
                problems.append('Open URL unexpectedly marked as needing confirmation')
            if not case['stableTop3']:
                problems.append('top cards still moving after settle')
            if blocked()[spy_before:]:
                problems.append('a blocked command was attempted while only typing')
            if problems:
                case['problems'] = problems
                fail(f'{label}: "{text}" gives a valid Open URL card', case=case)
            # Short Enter runs only a primed leader; otherwise the policy requires a held Enter.
            if case['leaderPrimed']:
                key('Enter', hold_ms=40)
                case['enter'] = 'short Enter (primed)'
            else:
                target_card('open_url')
                key('Enter', hold_ms=40)
                time.sleep(.4)
                if blocked()[spy_before:]:
                    fail(f'{label}: a short Enter on the unprimed Open URL card requests nothing', blocked=blocked()[spy_before:])
                key('Enter', hold_ms=260)
                case['enter'] = 'short Enter requested nothing (unprimed); held Enter'
            wait("return document.body.innerText.includes('Opened (isolated URL QA spy')||window.__uq.hides>" + str(hides_before), f'{label}: execute answered', seconds=5, record=False)
            case['feedbackAfterExecute'] = js(FEEDBACK)
            time.sleep(1.2)  # fire-and-forget actions hide the overlay after 450 ms
            calls = blocked()[spy_before:]
            case['spyCalls'] = calls
            case['hiddenAfterExecute'] = js('return window.__uq.hides') > hides_before
            if len(calls) != 1 or calls[0]['cmd'] != 'execute' or calls[0]['actionId'] != 'open_url' or calls[0]['keys'] != ['url'] \
                    or calls[0]['url'] != want_url or calls[0]['token']:
                fail(f'{label}: exactly one token-free execute(open_url, {{url: {want_url}}})', calls=calls)
            if not case['hiddenAfterExecute']:
                fail(f'{label}: overlay hid after the successful (spied) open', feedback=case['feedbackAfterExecute'])
            note(f'{label}: "{text}" -> model open_url {case["modelProbabilities"][:1]}, card open_url "{card["detail"]}" (p {case["leaderProbability"]}, c {case["leaderConfidence"]})'
                 f'{", primed" if case["leaderPrimed"] else ", unprimed"}; {case["enter"]} -> one execute url={want_url}; overlay hid; no can’t-run error')

        # ---- Malformed/dangerous inputs: never reach execute ----
        for label, text in BLOCKED:
            spy_before = len(blocked())
            ensure_shown()
            result = settle(text, label, False)
            case = case_record(text, False, result)
            evidence['cases'][label] = case
            problems = request_problems(case)
            card = case['openUrlCard']
            if card:
                if not card['error']:
                    problems.append('Open URL card offered without an argument error')
                if card['primed']:
                    problems.append('Open URL card primed for a malformed input')
                target_card('open_url')
                case['openUrlTargeted'] = js("return document.querySelector('#launcher-card-open_url')?.classList.contains('target')??false")
                case['cueWhenTargeted'] = js("return document.querySelector('#launcher-card-open_url .cue')?.textContent?.trim()??null")
                if case['cueWhenTargeted'] != 'can’t run':
                    problems.append('targeted Open URL card is not marked can’t run')
                key('Enter', hold_ms=260)
                time.sleep(.6)
                case['feedbackAfterEnter'] = js(FEEDBACK)
            if label == 'credentials':
                wd_shot(f'{label}-blocked')
            calls = blocked()[spy_before:]
            case['spyCalls'] = calls
            if calls:
                problems.append('a native command was requested for a malformed/dangerous input')
            if problems:
                case['problems'] = problems
                fail(f'{label}: "{text}" stays blocked', case=case)
            note(f'{label}: "{text}" -> model {case["modelTop"]}, leader {case["leader"]}; Open URL card '
                 + (f'error "{card["error"]}", held Enter on it requested nothing' if card else 'not offered') + '; no execute')

        # ---- Run-wide invariants ------------------------------------------------------
        spy = blocked()
        executes = [b for b in spy if b['cmd'] == 'execute']
        if len(executes) != len(VALID) or any(b['actionId'] != 'open_url' or b['token'] for b in executes) \
                or [b['url'] for b in executes] != [v[3] for v in VALID] or len(spy) != len(VALID) + 1:
            fail('only the two expected Open URL executes (plus self-test) reached the spy', blocked=spy)
        actions_log = (qa / 'actions.log').read_text().splitlines() if (qa / 'actions.log').exists() else []
        if actions_log:
            fail('no action stand-in (xdg-open, gio, browsers) or adapter was called', calls=actions_log)
        pm = js('return window.__uq.postMessage')
        if any(c in ('execute', 'propose_action', 'save_proposal', 'reload') for c in pm):
            fail('no blocked command took the postMessage path', postMessage=pm)
        ipc = js('return window.__uq.ipc')
        calls = (qa / 'calls.log').read_text().splitlines() if (qa / 'calls.log').exists() else []
        evidence['collectorCalls'] = {'wl-paste': sum(c.startswith('wl-paste') for c in calls), 'wl-copy': sum(c.startswith('wl-copy') for c in calls),
                                      'hyprctl': sorted({c for c in calls if c.startswith('hyprctl')})}
        if evidence['collectorCalls']['wl-copy']:
            fail('nothing copied to the clipboard')
        reqs = js('return window.__uq.requests')
        bridge = [r for r in reqs if r['host'].endswith(':7331')]
        if not all(r.get('refused') for r in bridge):
            fail('soft-if bridge reached only the refusing hook', bridge=bridge)
        decisions = js('return window.__uq.decisions')
        if any(d['sentinels'] for d in decisions) or not decisions:
            fail('no decision request carried ambient title or clipboard text', decisions=decisions[-5:])
        evidence['spy'] = spy
        evidence['ipcCommands'] = {c: ipc.count(c) for c in sorted(set(ipc))}
        evidence['postMessage'] = pm
        evidence['requestSummary'] = {'bridgeRefused': len(bridge), 'decisionRequests': len(decisions),
                                      'decisionRequestsWithAmbientText': sum(bool(d['sentinels']) for d in decisions),
                                      'otherHosts': sorted({r['host'] for r in reqs if r['host'] != LAB and not r['host'].endswith(':7331')})}
        note(f'run-wide: exactly {len(executes)} spied Open URL executes {[b["url"] for b in executes]}, 0 token-bound; no stand-in/adapter call; '
             f'clipboard never written; {len(bridge)} bridge requests refused; {len(decisions)} decision requests, none with ambient text')
        evidence['passed'] = True
    except Exception as error:  # noqa: BLE001 - evidence records any failure
        evidence['error'] = str(error)[:400]
        try:
            evidence['spyAtFailure'] = js('return window.__uq.blocked')
            evidence['statusAtFailure'] = js(STATUS)
            wd_shot('99-failure')
        except Exception:  # noqa: BLE001
            pass
    finally:
        if session:
            try:
                request(base, 'DELETE', f'/session/{session}', timeout=3)
            except Exception:  # noqa: BLE001
                pass
        if driver and driver.poll() is None:
            driver.terminate()
            try:
                driver.wait(timeout=3)
            except subprocess.TimeoutExpired:
                driver.kill()
        time.sleep(1)
        stray = [p for p in marked_pids(args.marker) if p != os.getppid()
                 and Path(proc_argv(p)[0] if proc_argv(p) else '').name not in ('dbus-daemon', 'dbus-run-session')]
        for p in stray:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
        evidence['innerCleanup'] = {'strayTerminated': [{'pid': p, 'argv0': Path(proc_argv(p)[0]).name if proc_argv(p) else '?'} for p in stray]}
        evidence['screenshots'] = shots
        args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'passed': evidence['passed'], 'checks': len(evidence['checks']), 'error': evidence.get('error'),
                      'failure': evidence.get('failure')}, indent=2, ensure_ascii=False)[:3000])
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
