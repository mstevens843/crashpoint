'use strict';
// Exercise the unmodified upstream public client. Retain wire bytes without authentication.
const fs = require('node:fs');
const path = require('node:path');
function append(value) {
  const fd = fs.openSync(process.env.CP_TRANSCRIPT, 'a');
  try { fs.writeSync(fd, JSON.stringify(value) + '\n'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
}
const realFetch = global.fetch;
global.fetch = async (url, options) => {
  append({event: 'request', url, method: options.method, body: options.body,
    headers: Object.fromEntries(Object.entries(options.headers).filter(([k]) => k !== 'Authorization')),
    authorization: 'omitted test-only credential'});
  try {
    const response = await realFetch(url, {...options, signal: AbortSignal.timeout(15000)});
    append({event: 'response', status: response.status, body: await response.clone().text()});
    return response;
  } catch (error) { append({event: 'transport_error', error: error.message}); throw error; }
};
const {RealityLayerAdapter} = require(path.join(process.env.CP_SUBJECT, 'integrations/langgraph/reality-layer-adapter.js'));
const client = new RealityLayerAdapter({baseUrl: process.env.CP_BASE_URL,
  token: process.env.REALITY_MCP_TOKEN, clientName: 'crashpoint-local-fixture'});
const request = JSON.parse(fs.readFileSync(process.env.CP_REQUEST, 'utf8'));
client.call(request.name, request.arguments).then(value => {
  console.log(JSON.stringify({ok: true, value}));
}).catch(error => {
  console.log(JSON.stringify({ok: false, error: error.message}));
  process.exitCode = 2;
});
