import crypto from "node:crypto";
import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import net from "node:net";
import path from "node:path";

type Mode = "naive" | "idem" | "nondet" | "twophase";
type Barrier = "b0" | "b1" | "b2" | "none";

export interface CrashpointRequest {
  ledger: string;
  intent: string;
  mode: Mode;
  barrier: Barrier;
  markerDir: string;
}

const PAYLOAD: Record<string, unknown> = { amount: 100, to: "acct-attacker" };
const KEY_SCHEME = "cp1key";

export async function crashpointWorkflow(req: CrashpointRequest) {
  "use workflow";

  const key = req.mode === "twophase" ? await prepareIdentity(req.intent) : undefined;
  await charge(req, key);
  await sentinel(req);
  return "ok";
}

async function prepareIdentity(intent: string) {
  "use step";

  return twoPhaseKey(intent);
}

async function charge(req: CrashpointRequest, keyOverride?: string) {
  "use step";

  if (req.barrier === "b0") {
    crashOnce(req);
  }
  await effect(req, keyOverride);
  if (req.barrier === "b1") {
    crashOnce(req);
  }
  return "effect-complete";
}

charge.maxRetries = 100;

async function sentinel(req: CrashpointRequest) {
  "use step";

  if (req.barrier === "b2") {
    crashOnce(req);
  }
  return "sentinel-complete";
}

sentinel.maxRetries = 100;

async function effect(req: CrashpointRequest, keyOverride?: string) {
  const payload: Record<string, unknown> = { ...PAYLOAD };
  const idempotent = ["idem", "nondet", "twophase"].includes(req.mode);
  const nondeterministic = ["nondet", "twophase"].includes(req.mode);
  if (nondeterministic) {
    payload.memo = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  }
  const key = keyOverride ?? (idempotent ? deriveIdempotencyKey("charge", req.intent, 1, payload) : null);
  await ledgerExecute(req.ledger, req.intent, key, payload);
}

function twoPhaseKey(intent: string) {
  return deriveIdempotencyKey("charge-prepared", intent, 1, PAYLOAD);
}

function deriveIdempotencyKey(
  namespace: string,
  subject: string,
  intentVersion: number,
  payload: Record<string, unknown>
) {
  const envelope = { n: namespace, s: subject, v: intentVersion, p: payload };
  return `${KEY_SCHEME}_${sha256(`${KEY_SCHEME}:${canonicalize(envelope)}`)}`;
}

function canonicalize(value: unknown): string {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("non-finite number");
    return Object.is(value, -0) ? "0" : String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalize(item)).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const raw = value as Record<string, unknown>;
    const members = Object.keys(raw)
      .sort()
      .filter((key) => raw[key] !== null)
      .map((key) => `${JSON.stringify(key)}:${canonicalize(raw[key])}`);
    return `{${members.join(",")}}`;
  }
  throw new Error(`cannot canonicalize ${typeof value}`);
}

function sha256(text: string) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

function ledgerExecute(
  socketPath: string,
  intent: string,
  key: string | null,
  payload: Record<string, unknown>
) {
  return new Promise<void>((resolve, reject) => {
    const client = net.createConnection(socketPath);
    let buffer = "";
    client.setEncoding("utf8");
    client.on("connect", () => {
      client.write(JSON.stringify({ op: "execute", intent_id: intent, key, payload }) + "\n");
    });
    client.on("data", (chunk) => {
      buffer += chunk;
      if (buffer.endsWith("\n")) {
        client.end();
      }
    });
    client.on("error", reject);
    client.on("end", () => {
      try {
        const response = JSON.parse(buffer);
        if (!response.ok) {
          reject(new Error(`ledger execute failed: ${response.error ?? "unknown"}`));
          return;
        }
        resolve();
      } catch (error) {
        reject(error);
      }
    });
  });
}

function crashOnce(req: CrashpointRequest) {
  const marker = path.join(req.markerDir, `crashed-${req.intent}-${req.barrier}.txt`);
  mkdirSync(req.markerDir, { recursive: true });
  if (existsSync(marker)) {
    return;
  }
  writeFileSync(marker, "crashed");
  process.kill(process.pid, "SIGKILL");
  throw new Error("unreachable");
}
