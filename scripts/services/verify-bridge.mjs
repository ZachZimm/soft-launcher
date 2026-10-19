#!/usr/bin/env node
// Exercise the installed bridge using temporary data and a random loopback port.
// No requests go to a model or to the user's port 7331 service.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const runtime = process.argv[2] ?? join(homedir(), '.local/lib/soft-launcher');
const manifest = JSON.parse(await readFile(join(runtime, 'install.json'), 'utf8'));
const directory = await mkdtemp(join(tmpdir(), 'soft-launcher-bridge-'));
const database = join(directory, 'fixture.db');
const policy = join(directory, 'policy.json');
await writeFile(policy, JSON.stringify({ bands: { act: 0.85, confirm: 0.6 }, branches: {} }));
let child;
let exited;
let url;

async function start() {
  child = spawn(manifest.node, [join(runtime, 'soft-if/dist/src/cli.js'), 'debug', '--db', database, '--policy', policy, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
  exited = once(child, 'exit');
  let output = '';
  let errors = '';
  child.stderr.on('data', chunk => { errors += chunk; });
  url = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Fixture bridge did not become ready')), 5000);
    child.stdout.on('data', chunk => {
      output += chunk;
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) { clearTimeout(timeout); resolve(match[0]); }
    });
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Fixture bridge exited ${code}: ${errors}`)); });
  });
  assert.deepEqual(await (await fetch(`${url}/health`)).json(), { ok: true, version: 1 });
}

async function stop() {
  if (!child) return;
  child.kill('SIGTERM');
  const timer = setTimeout(() => child?.kill('SIGKILL'), 5000);
  try {
    const [code, signal] = await exited;
    assert.equal(code, 0, `Fixture did not shut down cleanly: ${signal}`);
  } finally { clearTimeout(timer); child = undefined; }
}

try {
  await start();
  const controller = new AbortController();
  const sse = await fetch(`${url}/events`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]) });
  assert.equal(sse.status, 200);
  assert.match(sse.headers.get('content-type'), /text\/event-stream/);
  const reader = sse.body.getReader();
  let frame = '';
  while (!frame.includes('\n\n')) {
    const { value, done } = await reader.read();
    assert.equal(done, false);
    frame += new TextDecoder().decode(value);
  }
  assert.match(frame, /event: snapshot/);
  controller.abort();
  await reader.cancel().catch(() => undefined);
  const id = 'startup-service-synthetic-fixture';
  const ts = Date.now();
  const record = { id, ts, name: 'service-fixture', backend: 'fixture-no-model', tier: 1, emulated: false, latency_ms: 1, state: 'synthetic startup persistence fixture', questions: { local: { type: 'noul', instructions: 'Fixture only?' } }, answers: { local: { type: 'noul', p: 0.9, confidence: 0.9, probabilities: { yes: 0.9, no: 0.1 } } }, stale: false, attempts: [{ backend: 'fixture-no-model', tier: 1, latency_ms: 1 }] };
  const posted = await fetch(`${url}/events`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ events: [{ type: 'decision', data: record }, { type: 'outcome', data: { request_id: id, ts, payload: { accepted: true, fixture: true } } }] }) });
  assert.equal(posted.status, 200, await posted.text());
  const changed = await fetch(`${url}/policy`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ bands: { act: 0.9 } }) });
  assert.equal(changed.status, 200);
  assert.equal(JSON.parse(await readFile(policy, 'utf8')).bands.act, 0.9);
  await stop();
  const reopened = new DatabaseSync(database, { readOnly: true });
  assert.equal(reopened.prepare('SELECT id FROM requests WHERE id=?').get(id).id, id);
  assert.equal(reopened.prepare('SELECT count(*) AS count FROM outcomes WHERE request_id=?').get(id).count, 1);
  reopened.close();
  await start();
  const snapshot = await (await fetch(`${url}/snapshot?limit=10`)).json();
  assert.equal(snapshot.decisions[0].id, id);
  assert.equal(snapshot.outcomes[0].request_id, id);
  assert.equal(snapshot.policy.bands.act, 0.9);
  await stop();
  console.log('PASS installed bridge: loopback HTTP, SSE snapshot, SQLite decision/outcome across graceful restart, persisted temporary policy');
} finally {
  if (child) await stop();
  await rm(directory, { recursive: true, force: true });
}
