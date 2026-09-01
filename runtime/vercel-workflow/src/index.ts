import express from "express";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { getRun, start } from "workflow/api";
import { getWorld } from "workflow/runtime";
import { crashpointWorkflow, type CrashpointRequest } from "./workflows/crashpoint.js";

const app = express();

app.use(express.json());

let worldStarted: Promise<void> | undefined;

async function ensureWorldStarted() {
  if (!worldStarted) {
    worldStarted = getWorld().then(async (world) => {
      await world.start?.();
    });
  }
  await worldStarted;
}

function asRequest(value: unknown): CrashpointRequest {
  if (!value || typeof value !== "object") {
    throw new Error("request body must be an object");
  }
  const raw = value as Record<string, unknown>;
  const mode = raw.mode;
  const barrier = raw.barrier;
  if (!["naive", "idem", "nondet", "twophase"].includes(String(mode))) {
    throw new Error(`invalid mode: ${String(mode)}`);
  }
  if (!["b0", "b1", "b2", "none"].includes(String(barrier))) {
    throw new Error(`invalid barrier: ${String(barrier)}`);
  }
  for (const key of ["ledger", "intent", "markerDir"]) {
    if (typeof raw[key] !== "string" || raw[key] === "") {
      throw new Error(`${key} must be a non-empty string`);
    }
  }
  return {
    ledger: raw.ledger as string,
    intent: raw.intent as string,
    mode: mode as CrashpointRequest["mode"],
    barrier: barrier as CrashpointRequest["barrier"],
    markerDir: raw.markerDir as string,
  };
}

app.get("/api/health", async (_req, res) => {
  await ensureWorldStarted();
  res.json({ ok: true });
});

app.post("/api/start", async (req, res) => {
  try {
    await ensureWorldStarted();
    const body = asRequest(req.body);
    const run = await start(crashpointWorkflow, [body]);
    await mkdir(body.markerDir, { recursive: true });
    await writeFile(path.join(body.markerDir, `run-${body.intent}.txt`), run.runId);
    res.json({ runId: run.runId });
  } catch (error) {
    res.status(500).json({
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

app.get("/api/output/:runId", async (req, res) => {
  try {
    await ensureWorldStarted();
    const run = getRun(req.params.runId);
    const status = await run.status;
    if (status !== "completed") {
      res.status(202).json({ status });
      return;
    }
    res.json({ status, output: await run.returnValue });
  } catch (error) {
    res.status(500).json({
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

export default app;
