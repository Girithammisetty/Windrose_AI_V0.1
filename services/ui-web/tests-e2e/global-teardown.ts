import { readFileSync, existsSync, unlinkSync } from "node:fs";
import path from "node:path";

const PID_FILE = path.join(__dirname, ".e2e-pids.json");

export default async function globalTeardown() {
  if (!existsSync(PID_FILE)) return;
  try {
    const pids: number[] = JSON.parse(readFileSync(PID_FILE, "utf8"));
    for (const pid of pids) {
      try {
        // Kill the process group (pnpm start spawns tsx as a child).
        process.kill(-pid, "SIGTERM");
      } catch {
        try {
          process.kill(pid, "SIGTERM");
        } catch {
          /* already gone */
        }
      }
    }
  } finally {
    unlinkSync(PID_FILE);
  }
}
