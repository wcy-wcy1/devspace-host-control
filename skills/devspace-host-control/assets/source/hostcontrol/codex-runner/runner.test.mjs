import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { agentTracker, makeReport, runBounded, runJob } from './runner.mjs';

test('machine test failure is distinct from agent completion', () => {
  const report = makeReport({ job_id: 'fixture', commit: 'a'.repeat(40), mode: 'test', suite: ['node', '--test'] }, { completed: true, failed: false, commands: [] }, { exit_code: 0 }, { exit_code: 1, timed_out: false }, 'diagnosed failure', false);
  assert.equal(report.agent_completed, true);
  assert.equal(report.tests.passed, false);
  assert.equal(report.tests.exit_code, 1);
});

test('tracker requires a completed turn and retains bounded actual command events', () => {
  const tracker = agentTracker();
  tracker.accept(JSON.stringify({ type: 'thread.started', thread_id: 'fixture-thread' }));
  tracker.accept(JSON.stringify({ type: 'item.completed', item: { type: 'command_execution', command: 'npm test', exit_code: 1, aggregated_output: 'failed' } }));
  assert.equal(tracker.state.completed, false);
  tracker.accept(JSON.stringify({ type: 'turn.completed' }));
  assert.equal(tracker.state.completed, true);
  assert.equal(tracker.state.commands[0].exit_code, 1);
  tracker.accept(JSON.stringify({ type: 'turn.failed' }));
  assert.equal(tracker.state.failed, true);
  assert.throws(() => tracker.accept('not json'));
});

test('bounded child execution preserves actual exit code and caps output', async () => {
  const result = await runBounded(process.execPath, ['-e', 'process.stdout.write("x".repeat(300000)); process.stderr.write("fixture error"); process.exit(7)']);
  assert.equal(result.exit_code, 7);
  assert.equal(result.truncated, true);
  assert.ok(Buffer.byteLength(result.stdout) <= 96 * 1024);
  assert.match(result.stderr, /fixture error/);
});

test('child deadline stops execution without rerunning it', async () => {
  const result = await runBounded(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { timeoutMs: 100 });
  assert.equal(result.timed_out, true);
  assert.notEqual(result.exit_code, 0);
});

test('UTF-8 split across child writes stays intact in logs and JSON events', async () => {
  const lines = [];
  const source = `const data=Buffer.from(JSON.stringify({text:'中文🙂'})+'\\n');process.stdout.write(data.subarray(0,10));setTimeout(()=>process.stdout.write(data.subarray(10)),25);`;
  const result = await runBounded(process.execPath, ['-e', source], { onLine: line => lines.push(JSON.parse(line)) });
  assert.equal(result.stdout, JSON.stringify({ text: '中文🙂' }) + '\n');
  assert.deepEqual(lines, [{ text: '中文🙂' }]);
});

test('oversized individual agent events are refused', async () => {
  await assert.rejects(() => runBounded(process.execPath, ['-e', 'process.stdout.write("x".repeat(1024*1024+1))'], { onLine() {} }), /exceeds 1 MiB/);
});

async function fixture(t, mode = 'test') {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'hostcontrol-codex-runner-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  const paths = Object.fromEntries(['workspace', 'baseline', 'artifacts', 'auth', 'codexHome', 'scratch', 'home'].map(name => [name, path.join(dir, name)]));
  for (const key of ['workspace', 'baseline', 'artifacts', 'auth']) await fs.mkdir(paths[key]);
  await fs.writeFile(path.join(paths.workspace, 'app.txt'), 'fixture source');
  await fs.writeFile(path.join(paths.baseline, 'app.txt'), 'fixture source');
  await fs.writeFile(path.join(paths.auth, 'auth.json'), JSON.stringify({ OPENAI_API_KEY: 'fixture-not-a-real-secret' }));
  const request = { version: 1, job_id: 'a'.repeat(32), commit: 'b'.repeat(40), mode, instruction: 'Analyze fixture results', suite: [process.execPath, '-e', 'console.log("fixture failure");process.exit(1)'] };
  const inputPath = path.join(dir, 'request.json');
  await fs.writeFile(inputPath, JSON.stringify(request));
  const fake = path.join(dir, 'fake-codex.mjs');
  await fs.writeFile(fake, `
import { promises as fs } from 'node:fs';
import path from 'node:path';
let prompt='';for await (const data of process.stdin)prompt+=data;
if(!prompt.includes('Exact source commit:'))process.exit(2);
const destination=process.argv[process.argv.indexOf('--output-last-message')+1];
await fs.writeFile(destination,'Fixture agent analyzed the failed test.');
await fs.writeFile(path.join(path.dirname(destination),'fixture.png'),Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1kAAAAASUVORK5CYII=','base64'));
console.log(JSON.stringify({type:'thread.started',thread_id:'fixture-thread'}));
console.log(JSON.stringify({type:'turn.completed'}));
`);
  return { dir, paths, request, inputPath, fake };
}

test('full runner executes fixed tests and produces report plus real image bytes with fixture CLI', async t => {
  const f = await fixture(t);
  const report = await runJob({ ...f.paths, inputPath: f.inputPath, codexProgram: process.execPath, codexPrefix: [f.fake] });
  assert.equal(report.commit, f.request.commit);
  assert.equal(report.agent_completed, true);
  assert.equal(report.tests.exit_code, 1);
  assert.equal(report.tests.passed, false);
  assert.equal(await fs.readFile(path.join(f.paths.workspace, 'app.txt'), 'utf8'), 'fixture source');
  assert.match(await fs.readFile(path.join(f.paths.artifacts, 'test-output.txt'), 'utf8'), /fixture failure/);
  assert.equal((await fs.readFile(path.join(f.paths.artifacts, 'fixture.png'))).subarray(1, 4).toString(), 'PNG');
  assert.equal(JSON.parse(await fs.readFile(path.join(f.paths.artifacts, 'report.json'), 'utf8')).tests.passed, false);
});

test('incomplete or malformed agent events fail instead of accepting model prose', async t => {
  const f = await fixture(t, 'review');
  await fs.writeFile(f.fake, 'process.stdin.resume();process.stdin.on("end",()=>console.log("not-json"));');
  await assert.rejects(() => runJob({ ...f.paths, inputPath: f.inputPath, codexProgram: process.execPath, codexPrefix: [f.fake] }));
});

test('implementation produces an actual diff, including a newly created file', async t => {
  const f = await fixture(t, 'implement');
  await fs.appendFile(f.fake, '\nawait fs.writeFile("new-file.txt","new fixture file");');
  const report = await runJob({ ...f.paths, inputPath: f.inputPath, codexProgram: process.execPath, codexPrefix: [f.fake] });
  assert.equal(report.patch, 'changes.patch');
  const patch = await fs.readFile(path.join(f.paths.artifacts, 'changes.patch'), 'utf8');
  assert.match(patch, /new fixture file/);
  assert.match(patch, /new file mode/);
});
