const keepAlive = setInterval(() => undefined, 2 ** 31 - 1);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    clearInterval(keepAlive);
    process.exit(0);
  });
}

await import("./.output/server/index.mjs");
