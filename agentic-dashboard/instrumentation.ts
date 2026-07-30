// Started once per Next.js server instance. We boot the tick worker that
// keeps mock agents alive, advancing PnL, generating discoveries, etc.
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { startWorker } = await import("./lib/worker");
  startWorker();
}
