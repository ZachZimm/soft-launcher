#!/usr/bin/env python3
"""Routing UI QA for a launcher binary, inside the hidden agent workspace only.

Started by run-routing-ui-isolated-qa.py. The launcher runs on the workspace's
private Xvfb display under WebKitWebDriver with a private HOME, XDG profile and
dbus-run-session bus. Keys and pointer are DOM events in its own page.

Nothing is executed. An in-page IPC spy answers `execute`, `propose_action`,
`save_proposal` and `reload` itself, so native never receives them: an agent
first Enter gets a synthetic confirmation (the UI's mandatory confirmation
panel), and every other blocked command gets a synthetic refusal. The spy
answers through Tauri's custom-protocol response instead of throwing, because
a failed IPC fetch would make Tauri fall back to postMessage and reach native.
Behind the spy, the adapter and PATH are public logging stand-ins (Hermes,
xdg-open, browsers, ssh, terminals, session/media/network tools) that refuse.

Ambient context is a public task-like fixture: a window title and clipboard
text that ask for work. Every decision request body sent to lab Clef is
checked to contain neither, while still carrying the window class and
clipboard kind. soft-if logging to the host debugger bridge is refused in the
page, after rebuilding the runtime behind the hook while hidden.
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

# (label, exact input, expected leading action, expectation kind)
EXACT = [
    ('full-question', 'Is there a transcription server on lab?', 'send_to_agent'),
    ('keywords', 'transcription model', 'web_search'),
    ('setup', 'set up deadlock mod manager', 'send_to_agent'),
]
CONCRETE = [
    ('app', 'firefox', 'open_app'),
    ('app-setup', 'set up firefox', 'send_to_agent'),
    ('calc', '12*7', 'calc'),
    ('calc-question', 'What is 2+2?', 'calc'),
    ('ssh', 'ssh lab', 'ssh_to_host'),
    ('url', 'https://example.com/docs', 'open_url'),
]
SPY_REFUSAL = 'Refused by isolated routing QA spy (native not invoked)'
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
                  if e.name.isdecimal() and int(e.name) != os.getpid() and proc_env(int(e.name)).get('SL_RT_QA_RUN') == marker)


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
const qa=window.__rt??{requests:[],decisions:[],ipc:[],blocked:[],postMessage:[],hides:0};window.__rt=qa;
const SENT=%SENTINELS%;const CLASS=%CLASS%;const REFUSAL=%REFUSAL%;
const BLOCK=new Set(['execute','propose_action','save_proposal','reload','qa_spy_probe']);
const reply=(ok,body)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json','Tauri-Response':ok?'ok':'error'}});
const originalFetch=window.__rtFetch??window.fetch;window.__rtFetch=originalFetch;
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
  const row={cmd,actionId:r.actionId??null,keys:Object.keys(r.args??{}).sort(),token:!!r.confirmationToken,at:Date.now(),inputLength:typeof input==='string'?input.length:null,inputSha256:null};
  if(typeof input==='string'){const dg=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(input));row.inputSha256=[...new Uint8Array(dg)].map(b=>b.toString(16).padStart(2,'0')).join('');}
  qa.blocked.push(row);
  if(cmd==='execute'&&r.actionId==='send_to_agent'&&!r.confirmationToken&&!r.expectedApp){
   row.answer='synthetic confirmationRequired';
   return reply(true,{status:'confirmationRequired',actionId:'send_to_agent',message:'Confirm to send (QA spy; native not invoked)',confirmationToken:'qa-spy-'+crypto.randomUUID(),expiresInMs:15000});
  }
  row.answer='synthetic refusal';
  return reply(false,REFUSAL);
 }
 return originalFetch(...args);
};
if(!window.__rtES){window.__rtES=window.EventSource;window.EventSource=function(url,...rest){const t=new URL(url,location.href);qa.requests.push({host:t.host,path:t.pathname,refused:t.port==='7331',sse:true});if(t.port==='7331')throw new TypeError('EventSource refused by isolated QA');return new window.__rtES(url,...rest);};}
try{if(window.ipc&&!window.__rtPM){const pm=window.ipc.postMessage.bind(window.ipc);window.__rtPM=pm;window.ipc.postMessage=function(data){let cmd='?';try{cmd=JSON.parse(data).cmd;}catch(e){}qa.postMessage.push(cmd);if(BLOCK.has(cmd))return;return pm(data);};qa.postMessageWrapped=true;}}catch(e){qa.postMessageWrapped=false;}
return true;
"""
CARDS = r"""
return [...document.querySelectorAll('#launcher-row .card')].map(c=>({id:c.id.replace('launcher-card-',''),leader:c.classList.contains('leader'),
 primed:c.classList.contains('primed'),target:c.classList.contains('target'),label:c.getAttribute('aria-label'),
 num:c.querySelector('.num')?.textContent?.trim()??null,confirmTag:!!c.querySelector('.tag.warn'),
 detail:c.querySelector('.detail')?.textContent?.trim()??'',x:Number((c.style.transform.match(/translateX\(([-\d.]+)px/)||[])[1]??NaN),
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
    if os.environ.get('SL_RT_QA') != '1' or os.environ.get('SL_RT_QA_RUN') != args.marker:
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
        'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'stage': args.stage,
        'binary': str(args.binary),
        'catalogueSource': str(args.catalogue), 'catalogueCopySha256': sha(catalogue.read_bytes()),
        'isolation': {'display': args.display, 'home': str(home), 'privateRuntime': os.environ['XDG_RUNTIME_DIR'], 'privateBus': bus.split(',')[0],
                      'xdgDataDirs': '/usr/local/share:/usr/share', 'hermesAdapter': 'logging stand-in (refuses)'},
        'ambientFixture': {'windowClass': WINDOW_CLASS, 'windowTitle': WINDOW_TITLE, 'clipboard': CLIP_TEXT},
        'checks': [], 'cases': {},
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
        if not native_pid or proc_env(native_pid).get('SL_RT_QA_RUN') != args.marker:
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
            return js('return window.__rt.blocked')

        def show():
            subprocess.run([str(args.binary), '--toggle'], env={k: v for k, v in env.items() if k != 'TAURI_WEBVIEW_AUTOMATION'},
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            time.sleep(.4)

        def settle(text, label, typed):
            """Enter `text` (per character when `typed`), wait for a fresh ready decision and a stable row."""
            decisions_before = len(js('return window.__rt.decisions'))
            js('window.__rt.expect=' + json.dumps(text) + ';return true')
            set_value('')
            time.sleep(.3)
            during = []
            if typed:
                for i in range(1, len(text) + 1):
                    set_value(text[:i])
                    time.sleep(.045)  # faster than the 80 ms debounce, like quick typing
                    if i % 6 == 0:
                        during.append(js(STATUS))
            else:
                set_value(text)
            time.sleep(.15)
            wait(f"const s=document.querySelector('.state[role=status]')?.textContent?.trim();const i=document.querySelector('#launcher-input').value;"
                 f"return s==='ready'&&i==={json.dumps(text)}&&document.querySelectorAll('#launcher-row .card').length>0",
                 f'{label}: fresh ready decision for the exact input', seconds=20, record=False)
            first = js(CARDS)
            samples = []
            for _ in range(12):  # 1.2 s: hysteresis settle ticks are 180 ms
                time.sleep(.1)
                samples.append([c['id'] for c in sorted(js(CARDS), key=lambda c: c['x'])][:3])
            cards = js(CARDS)
            decisions = js('return window.__rt.decisions')[decisions_before:]
            return {'cards': cards, 'firstLeader': next((c['id'] for c in first if c['leader']), None),
                    'stableTop3': all(s == samples[-1] for s in samples[-6:]), 'samples': samples, 'statusDuringTyping': during,
                    'decisionRequests': len(decisions), 'decisionBodies': decisions, 'apps': js(APPS), 'status': js(STATUS)}

        wait("return !!window.__TAURI_INTERNALS__&&!!document.querySelector('#launcher-input')", 'real Tauri webview mounted (hidden start)')
        origin = js('return location.origin')
        if origin in ('http://127.0.0.1:5173', 'http://localhost:5173'):
            fail('embedded production assets', origin=origin)
        js(HOOK.replace('"%CLEF_HOST%"', json.dumps(LAB)).replace('%SENTINELS%', json.dumps(list(SENTINELS))).replace('%CLASS%', json.dumps(WINDOW_CLASS)).replace('%REFUSAL%', json.dumps(SPY_REFUSAL)))
        # The spy must answer a blocked command itself before any Enter is pressed.
        probe = js("return window.__TAURI_INTERNALS__.invoke('qa_spy_probe',{}).then(()=>'resolved',e=>String(e))")
        if probe != SPY_REFUSAL or js('return window.__rt.blocked.length') != 1:
            fail('IPC spy answers blocked commands in the page (self-test)', probe=probe)
        note('IPC spy self-test: blocked command answered in the page, not by native', postMessageWrapped=js('return window.__rt.postMessageWrapped??null'))
        # soft-if's bridge captures fetch at mount; rebuild the runtime behind the hook while hidden.
        js("window.dispatchEvent(new KeyboardEvent('keydown',{key:',',ctrlKey:true,bubbles:true}));return true")
        wait("return !!document.querySelector('.settings')", 'settings open while hidden (Ctrl+comma in page)')
        if not js("return document.querySelector('#s-numbers').checked"):
            js("document.querySelector('#s-numbers').click();return true")
        js("[...document.querySelectorAll('.presets button')].find(b=>b.textContent.includes('Clef on lab')).click();return true")
        time.sleep(.1)
        js('window.__rt.requests=[];return true')
        js("[...document.querySelectorAll('.settings button')].find(b=>b.textContent.includes('Apply and reconnect')).click();return true")
        time.sleep(.5)
        wait("return !!document.querySelector('#launcher-input')&&!document.querySelector('.settings')", 'runtime rebuilt behind the hook while hidden; probabilities shown on cards', seconds=10)
        show()
        wait("return document.querySelector('.state[role=status]')?.textContent?.trim()!=='warming'||false", 'shown; warmup settled', seconds=120)
        note('embedded production assets; one private launcher', origin=origin)

        def check_case(label, text, expect, typed, shot=None):
            spy_before = len(blocked())
            result = settle(text, label, typed)
            cards = result['cards']
            ordered = sorted(cards, key=lambda c: c['x'])
            leader = next((c for c in cards if c['leader']), None)
            final_top = (result['decisionBodies'][-1].get('top') if result['decisionBodies'] else None) or []
            model_top = final_top[0][0] if final_top else None
            nums = {c['id']: parse_num(c['num']) for c in cards}
            top_p = max(cards, key=lambda c: nums[c['id']][0] or 0)
            p, conf = nums[leader['id']] if leader else (None, None)
            leak = [c['id'] for c in cards if 'Catalogue operation' in c['detail'] or 'installedAppMatch' in c['detail']
                    or 'precedence' in c['detail'].lower()]
            bodies = result['decisionBodies']
            case = {'input': text, 'expected': expect, 'typedPerCharacter': typed, 'leader': leader and leader['id'],
                    'leaderProbability': p, 'leaderConfidence': conf, 'leaderPrimed': leader and leader['primed'],
                    'leaderNeedsConfirmation': leader and leader['confirmTag'], 'leaderLabel': leader and leader['label'],
                    'leaderIsMaxProbability': bool(leader) and (nums[top_p['id']][0] or 0) <= (p or 0) + 1e-9,
                    'stableTop3': result['stableTop3'], 'top3': [c['id'] for c in ordered[:3]],
                    'modelTop': model_top, 'modelProbabilities': final_top,
                    'openAppCardShown': any(c['id'] == 'open_app' for c in cards),
                    'cards': [{k: c[k] for k in ('id', 'num', 'primed', 'confirmTag')} for c in ordered[:6]],
                    'apps': result['apps'][:4], 'routingTextOnCards': leak,
                    'decisionRequests': result['decisionRequests'],
                    'ambientInDecisionRequests': sorted({s for d in bodies for s in d['sentinels']}),
                    'decisionRequestShape': [{k: d.get(k) for k in ('input', 'hasExpectedInput', 'hasWindowClass', 'hasClipboardKind', 'hasTitleKey', 'hasHeadKey', 'hasInstalledAppMatch', 'status', 'bytes')} for d in bodies[-3:]],
                    'statusDuringTyping': result['statusDuringTyping']}
            evidence['cases'][label] = case
            if shot:
                wd_shot(shot)
            problems = []
            if model_top != expect:
                problems.append(f'model top choice {model_top} != {expect}')
            if expect == 'open_app':
                # The UI hides the open_app card while matching app cards show; the app card is the route.
                app_target = next((a for a in case['apps'] if a['target']), None)
                case['routedTo'] = app_target and app_target['id']
                if case['openAppCardShown'] or not app_target or 'Firefox' not in app_target['label'] or 'Enter opens it' not in app_target['label']:
                    problems.append('Firefox app card is not the Enter target (open_app card hidden by design)')
            elif case['leader'] != expect:
                problems.append(f"leader card {case['leader']} != {expect}")
            if expect != 'open_app' and not case['leaderIsMaxProbability']:
                problems.append('leader card is not the highest-probability card')
            if not case['stableTop3']:
                problems.append('top cards still moving after settle')
            if leak:
                problems.append('model-only routing text shown on cards')
            if case['ambientInDecisionRequests']:
                problems.append('ambient title/clipboard text reached a decision request')
            final = bodies[-1] if bodies else {}
            if not bodies or not final.get('hasExpectedInput') or not final.get('hasWindowClass') or final.get('hasTitleKey') or final.get('hasHeadKey'):
                problems.append('final decision request is not the class/kind-only projection of this input')
            if expect in ('send_to_agent', 'ssh_to_host') and (case['leaderPrimed'] or not case['leaderNeedsConfirmation']
                                                              or 'needs confirmation' not in (case['leaderLabel'] or '')):
                problems.append('guarded leader is primed or not marked as needing confirmation')
            if expect == 'send_to_agent' and '→ Hermes Desktop' not in (case['leaderLabel'] or ''):
                problems.append('agent card does not name Hermes Desktop')
            if expect == 'calc' and leader and '=' not in (leader['label'] or ''):
                case['calcPreview'] = 'absent'
            elif expect == 'calc' and leader:
                case['calcPreview'] = leader['label']
            if expect != 'send_to_agent' and any(c['id'] == 'send_to_agent' and c['primed'] for c in cards):
                problems.append('agent card primed')
            if blocked()[spy_before:]:
                problems.append('a blocked command was attempted while only typing')
            case['problems'] = problems
            if problems:
                fail(f'{label}: "{text}" routes to {expect}', case=case)
            note(f'{label}: "{text}" -> model {model_top} {final_top[:1]}, card {case.get("routedTo") or case["leader"]} (p {p}, c {conf}){", primed" if case["leaderPrimed"] else ""}'
                 f'{", needs confirmation" if case["leaderNeedsConfirmation"] else ""}; stable; no ambient text in request')
            return case

        def agent_confirmation(label, text):
            """Short Enter does nothing; held Enter gets the spy's confirmation; Escape cancels; native never called."""
            spy_before = len(blocked())
            js("const c=document.querySelector('#launcher-card-send_to_agent');const r=c.getBoundingClientRect();const o={bubbles:true,button:0,pointerId:1,clientX:r.left+r.width/2,clientY:r.top+r.height/2};c.dispatchEvent(new PointerEvent('pointerdown',o));c.dispatchEvent(new PointerEvent('pointerup',o));return true")
            time.sleep(.15)
            if not js("const c=document.querySelector('#launcher-card-send_to_agent');return c.classList.contains('target')&&!c.classList.contains('primed')"):
                fail(f'{label}: agent card targeted and unprimed')
            key('Enter', hold_ms=40)
            time.sleep(.5)
            short = blocked()[spy_before:]
            if short or js("return !!document.querySelector('[role=alertdialog]')"):
                fail(f'{label}: a short Enter on the unprimed agent card requests nothing', blocked=short)
            key('Enter', hold_ms=260)
            wait("return document.body.innerText.includes('Send to Hermes Desktop?')", f'{label}: held Enter shows "Send to Hermes Desktop?"', record=False)
            panel = js("return document.querySelector('[role=alertdialog]')?.innerText??''")
            want = (text, 'Clipboard is not sent', 'Hermes Desktop on this computer, as a new conversation', 'Press Enter again to send')
            if not all(w in panel for w in want) or CLIP_TEXT[:12] in panel:
                fail(f'{label}: confirmation shows exact text, destination and clipboard exclusion', panel=panel[:400])
            wd_shot(f'{label}-confirmation')
            key('Escape')
            time.sleep(.5)
            if js("return !!document.querySelector('[role=alertdialog]')"):
                fail(f'{label}: Escape dismissed the confirmation')
            time.sleep(1)
            calls = blocked()[spy_before:]
            want_sha = sha(text.encode())
            if len(calls) != 1 or calls[0]['cmd'] != 'execute' or calls[0]['actionId'] != 'send_to_agent' or calls[0]['token'] \
                    or calls[0]['inputSha256'] != want_sha or calls[0]['keys'] != ['input']:
                fail(f'{label}: one token-free agent request for the exact text, answered by the spy', blocked=calls)
            evidence['cases'][label]['confirmation'] = {'spyCalls': calls, 'panelHasExactText': True}
            note(f'{label}: short Enter requested nothing; held Enter -> spy confirmation panel with exact text and "Clipboard is not sent"; '
                 'Escape cancelled; no token-bound request; native execute never invoked')

        for label, text, expect in EXACT:
            check_case(label, text, expect, typed=True, shot=f'{label}-cards')
            agent_cards = [c for c in evidence['cases'][label]['cards'] if c['id'] == 'send_to_agent']
            if expect == 'send_to_agent':
                agent_confirmation(label, text)
            elif agent_cards and agent_cards[0]['primed']:
                fail(f'{label}: agent card never primed')
        for label, text, expect in CONCRETE:
            check_case(label, text, expect, typed=False, shot=f'{label}-cards' if label in ('app', 'calc', 'ssh', 'url') else None)

        # ---- Run-wide invariants ------------------------------------------------------
        spy = blocked()
        executes = [b for b in spy if b['cmd'] == 'execute']
        if any(b['token'] for b in executes) or any(b['actionId'] != 'send_to_agent' for b in executes) or len(executes) != 2:
            fail('only the two agent confirmation requests reached the spy; nothing token-bound', blocked=spy)
        actions_log = (qa / 'actions.log').read_text().splitlines() if (qa / 'actions.log').exists() else []
        if actions_log:
            fail('no action stand-in or adapter was called', calls=actions_log)
        pm = js('return window.__rt.postMessage')
        if any(c in ('execute', 'propose_action', 'save_proposal', 'reload') for c in pm):
            fail('no blocked command took the postMessage path', postMessage=pm)
        ipc = js('return window.__rt.ipc')
        if ipc.count('get_state') < len(EXACT) + len(CONCRETE):
            fail('IPC stayed on the spied custom protocol', ipc=sorted(set(ipc)))
        calls = (qa / 'calls.log').read_text().splitlines() if (qa / 'calls.log').exists() else []
        evidence['collectorCalls'] = {'wl-paste': sum(c.startswith('wl-paste') for c in calls), 'wl-copy': sum(c.startswith('wl-copy') for c in calls),
                                      'hyprctl': sorted({c for c in calls if c.startswith('hyprctl')})}
        if evidence['collectorCalls']['wl-copy']:
            fail('nothing copied to the clipboard')
        if any(c.startswith('hyprctl') and 'activewindow' not in c and 'clients' not in c for c in calls):
            fail('hyprctl was only read (activewindow/clients)', calls=sorted(set(calls)))
        reqs = js('return window.__rt.requests')
        bridge = [r for r in reqs if r['host'].endswith(':7331')]
        if not all(r.get('refused') for r in bridge):
            fail('soft-if bridge reached only the refusing hook', bridge=bridge)
        decisions = js('return window.__rt.decisions')
        if any(d['sentinels'] for d in decisions) or not decisions:
            fail('no decision request carried ambient title or clipboard text', decisions=decisions[-5:])
        evidence['spy'] = spy
        evidence['ipcCommands'] = {c: ipc.count(c) for c in sorted(set(ipc))}
        evidence['postMessage'] = pm
        evidence['requestSummary'] = {'bridgeRefused': len(bridge), 'decisionRequests': len(decisions),
                                      'decisionRequestsWithAmbientText': sum(bool(d['sentinels']) for d in decisions),
                                      'otherHosts': sorted({r['host'] for r in reqs if r['host'] != LAB and not r['host'].endswith(':7331')})}
        note('run-wide: native execute never invoked (2 spy confirmations, 0 token-bound); no stand-in/adapter call; clipboard never written; '
             f'{len(bridge)} bridge requests refused; {len(decisions)} decision requests, none with ambient text')
        evidence['passed'] = True
    except Exception as error:  # noqa: BLE001 - evidence records any failure
        evidence['error'] = str(error)[:400]
        try:
            evidence['spyAtFailure'] = js('return window.__rt.blocked')
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
