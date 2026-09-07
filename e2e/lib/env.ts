/**
 * Single source of truth for every path/env decision the E2E harness makes.
 * playwright.config.ts, global-setup.ts and global-teardown.ts all import
 * this instead of each re-deriving the same paths — the classic way two of
 * the three quietly drift onto different scratch DB files.
 */
import * as path from "path";
import * as fs from "fs";
import * as dotenv from "dotenv";

// Load e2e/.env if present. Real environment variables (e.g. set by CI)
// always win — dotenv never overrides an already-set process.env value.
dotenv.config({ path: path.resolve(__dirname, "..", ".env") });

export const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
export const E2E_ROOT = path.resolve(__dirname, "..");

function envBool(name: string, fallback: boolean): boolean {
  const raw = process.env[name];
  if (raw === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(raw.toLowerCase());
}

function envInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

/** `.venv/Scripts/python.exe` on Windows, `.venv/bin/python` everywhere else — see
 * the repo's own memory note: the venv here is `.venv`, not `venv`, and bare
 * `python`/`py` are not guaranteed to resolve to it. */
function defaultPythonExecutable(): string {
  const venvPython =
    process.platform === "win32"
      ? path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
      : path.join(PROJECT_ROOT, ".venv", "bin", "python");
  return venvPython;
}

export interface E2EConfig {
  projectRoot: string;
  pythonExecutable: string;
  /** true unless E2E_BASE_URL points the suite at an already-running instance. */
  manageServer: boolean;
  port: number;
  baseURL: string;
  dbPath: string;
  /** SQLAlchemy URL form of dbPath, forward-slashed so it's valid on Windows too. */
  databaseUrl: string;
  seedScript: string;
  seedDataFile: string;
  serverLogFile: string;
  serverPidFile: string;
  adminStorageStatePath: string;
  keepDb: boolean;
  secretKey: string;
}

export function resolveConfig(): E2EConfig {
  const explicitBaseUrl = process.env.E2E_BASE_URL;
  const manageServer = !explicitBaseUrl;
  const port = envInt("E2E_PORT", 5000);
  const baseURL = explicitBaseUrl ?? `http://127.0.0.1:${port}`;

  const tmpDir = path.resolve(E2E_ROOT, ".tmp");
  const dbPath = process.env.E2E_DB_PATH
    ? path.resolve(process.env.E2E_DB_PATH)
    : path.join(tmpDir, "e2e_test.db");

  // sqlite:///C:/Users/... — SQLAlchemy wants forward slashes even on Windows.
  const databaseUrl = `sqlite:///${dbPath.split(path.sep).join("/")}`;

  const pythonExecutable = process.env.PYTHON_EXECUTABLE || defaultPythonExecutable();

  return {
    projectRoot: PROJECT_ROOT,
    pythonExecutable,
    manageServer,
    port,
    baseURL,
    dbPath,
    databaseUrl,
    seedScript: path.join(E2E_ROOT, "seed", "seed_e2e_data.py"),
    seedDataFile: process.env.E2E_SEED_DATA_FILE
      ? path.resolve(process.env.E2E_SEED_DATA_FILE)
      : path.join(E2E_ROOT, "seed", "e2e-data.json"),
    serverLogFile: path.join(tmpDir, "flask-server.log"),
    serverPidFile: path.join(tmpDir, "flask-server.pid"),
    adminStorageStatePath: path.join(tmpDir, "storage-state", "admin.json"),
    keepDb: envBool("E2E_KEEP_DB", false),
    secretKey: process.env.E2E_SECRET_KEY || "e2e-test-secret-key",
  };
}

export function ensureTmpDir(cfg: E2EConfig): void {
  fs.mkdirSync(path.dirname(cfg.dbPath), { recursive: true });
}
