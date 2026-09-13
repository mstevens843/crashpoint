import { randomUUID } from 'node:crypto';
import http from 'node:http';
import net from 'node:net';

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { z } from 'zod';

const host = process.env.SIDECAR_HOST ?? '127.0.0.1';
const port = Number(process.env.SIDECAR_PORT ?? '4891');
const ledgerSocket = required('CRASHPOINT_LEDGER_INVOKE');
const subjectPid = Number(required('CRASHPOINT_SUBJECT_PID'));
const crashMarker = required('CRASHPOINT_CRASH_MARKER');
const effectMode = process.env.CRASHPOINT_EFFECT_MODE ?? 'naive';

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function writeJson(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(body),
  });
  response.end(body);
}

function ledgerExecute(payload) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(ledgerSocket);
    let response = '';
    socket.setEncoding('utf8');
    socket.on('connect', () => socket.end(`${JSON.stringify(payload)}\n`));
    socket.on('data', chunk => {
      response += chunk;
    });
    socket.on('error', reject);
    socket.on('end', () => {
      try {
        resolve(JSON.parse(response));
      } catch (error) {
        reject(new Error(`invalid ledger response: ${response}`, { cause: error }));
      }
    });
  });
}

function modelResponse(messages) {
  const toolResults = messages.filter(message => message.role === 'tool');
  const lastToolResult = toolResults.at(-1);
  const retryOpenCall =
    typeof lastToolResult?.content === 'string' && lastToolResult.content.includes('Tool call was not executed');
  if (toolResults.length === 0 || retryOpenCall) {
    return {
      id: `chatcmpl_${randomUUID()}`,
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: 'crashpoint-model',
      choices: [
        {
          index: 0,
          finish_reason: 'tool_calls',
          message: {
            role: 'assistant',
            content: null,
            tool_calls: [
              {
                id: `call_${randomUUID()}`,
                type: 'function',
                function: {
                  name: 'effect',
                  arguments: JSON.stringify({ intent: 'trueforge-effect', value: 'charge-100' }),
                },
              },
            ],
          },
        },
      ],
      usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 },
    };
  }
  return {
    id: `chatcmpl_${randomUUID()}`,
    object: 'chat.completion',
    created: Math.floor(Date.now() / 1000),
    model: 'crashpoint-model',
    choices: [
      {
        index: 0,
        finish_reason: 'stop',
        message: { role: 'assistant', content: 'completed' },
      },
    ],
    usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 },
  };
}

function writeModelResponse(response, value, stream) {
  if (!stream) {
    writeJson(response, 200, value);
    return;
  }
  response.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  });
  const choice = value.choices[0];
  const first = {
    id: value.id,
    object: 'chat.completion.chunk',
    created: value.created,
    model: value.model,
    choices: [{ index: 0, delta: choice.message, finish_reason: null }],
  };
  const last = {
    id: value.id,
    object: 'chat.completion.chunk',
    created: value.created,
    model: value.model,
    choices: [{ index: 0, delta: {}, finish_reason: choice.finish_reason }],
    usage: value.usage,
  };
  response.write(`data: ${JSON.stringify(first)}\n\n`);
  response.write(`data: ${JSON.stringify(last)}\n\n`);
  response.end('data: [DONE]\n\n');
}

function createMcpServer() {
  const mcp = new McpServer({ name: 'crashpoint-effect-server', version: '1.0.0' });
  mcp.registerTool(
    'effect',
    {
      description: 'Commit the crashpoint fixture effect.',
      inputSchema: {
        intent: z.string(),
        value: z.string(),
      },
    },
    async ({ intent, value }) => {
      const key = effectMode === 'idempotent' ? `${intent}:logical-action-1` : null;
      const result = await ledgerExecute({
        op: 'execute',
        intent_id: intent,
        key,
        payload: { value },
      });

      const fs = await import('node:fs/promises');
      try {
        await fs.writeFile(crashMarker, 'crossed\n', { flag: 'wx' });
        process.kill(subjectPid, 'SIGKILL');
        await new Promise(resolve => setTimeout(resolve, 30_000));
      } catch (error) {
        if (error?.code !== 'EEXIST') throw error;
      }

      return { content: [{ type: 'text', text: JSON.stringify(result) }] };
    },
  );
  return mcp;
}

const server = http.createServer(async (request, response) => {
  try {
    if (request.method === 'GET' && request.url === '/health') {
      return writeJson(response, 200, { ok: true });
    }
    if (request.method === 'POST' && request.url === '/v1/chat/completions') {
      let body = '';
      for await (const chunk of request) body += chunk;
      const parsed = JSON.parse(body);
      return writeModelResponse(response, modelResponse(parsed.messages ?? []), parsed.stream === true);
    }
    if (request.url === '/mcp') {
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      response.on('close', () => transport.close());
      const mcp = createMcpServer();
      await mcp.connect(transport);
      await transport.handleRequest(request, response);
      return;
    }
    writeJson(response, 404, { error: 'not found' });
  } catch (error) {
    writeJson(response, 500, { error: error instanceof Error ? error.message : String(error) });
  }
});

server.listen(port, host, () => {
  process.stdout.write(`sidecar ready http://${host}:${port}\n`);
});
