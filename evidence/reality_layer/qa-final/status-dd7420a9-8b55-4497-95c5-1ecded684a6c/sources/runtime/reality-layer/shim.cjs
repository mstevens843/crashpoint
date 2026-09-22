'use strict';
// Only replace the exported virtual Beta adapter, before the production registry loads it.
// The legacy adapter seam has no IR parameter: bind IDs from the pre-dispatch caller record,
// and require its exact device/capability/args. The controller separately snapshots STARTED IR ID.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = process.env.CP_SUBJECT;
function emit(event, fields = {}) {
  console.log(JSON.stringify({event, pid: process.pid, ...fields}));
}
async function barrier(event, fields) {
  emit(event, fields);
  await new Promise((_, reject) => setTimeout(() => reject(new Error('barrier deadline')), 30000));
}
const beta = require(path.join(root, 'src/adapters/virtual-light-beta.js'));
beta.execute = async (device, capability, args) => {
  const caller = JSON.parse(fs.readFileSync(process.env.CP_CALLER, 'utf8'));
  const payload = {device_id: device.id, capability, args};
  require('node:assert').deepStrictEqual(payload, caller.payload, 'adapter binding mismatch');
  const attempt_id = crypto.randomUUID();
  const fields = {action_id: caller.action_id, attempt_id, payload};
  emit('adapter_entered', fields);
  if (process.env.CP_CASE === 'pre_crash') await barrier('pre_effect_barrier', fields);
  const response = await fetch(process.env.CP_RECEIVER + '/execute', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...fields, run_id: caller.run_id, trial_id: caller.trial_id}),
    signal: AbortSignal.timeout(10000),
  });
  const ack = await response.json();
  if (!response.ok || ack.ok !== true) throw new Error('receiver rejected effect');
  emit('effect_ack', {...fields, ack});
  if (process.env.CP_CASE === 'post_crash') await barrier('post_effect_barrier', fields);
  if (['ordinary_error', 'unknown_error'].includes(process.env.CP_CASE)) {
    const error = new Error('injected completion loss after receiver fsync');
    if (process.env.CP_CASE === 'unknown_error') error.executionOutcomeUnknown = true;
    throw error;
  }
  device.state.power = 'on';
  return {state: {...device.state}, native_trace: {fixture: 'fsynced-receiver', attempt_id}};
};
if (process.env.CP_FAILPOINT === 'startup') {
  emit('injected_startup_failure');
  process.exit(71);
}
const {server} = require(path.join(root, 'server.js'));
server.listen(0, '127.0.0.1', () => emit('runtime_ready', {
  port: server.address().port, node: process.version, live: process.env.REALITY_LIVE,
  dry_run: process.env.PC_ADAPTER_DRY_RUN,
}));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
