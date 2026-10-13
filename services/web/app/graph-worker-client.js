// Next.js bundles the worker from this pattern; the map imports only this factory.
export function createGraphWorker() {
  return new Worker(new URL("./graph-worker.js", import.meta.url));
}
