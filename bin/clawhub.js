#!/usr/bin/env node
"use strict";

/**
 * clawhub — a small, self-contained implementation of `clawhub install`.
 *
 * It talks to the public ClawHub registry (https://clawhub.ai, the same one the
 * official `npx clawhub` CLI uses) to resolve a skill by slug, download its
 * archive, extract it into a local skills directory, and record what was
 * installed in a lockfile — mirroring the behaviour documented at clawhub.ai.
 *
 * Only Node.js built-ins are used, so it runs with `node bin/clawhub.js ...`
 * or, once linked, as `clawhub ...` / `npx clawhub ...`.
 */

const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const crypto = require("crypto");
const { execFileSync } = require("child_process");

const DEFAULT_REGISTRY = "https://clawhub.ai";
const DOT_DIR = ".clawhub";
const USER_AGENT = "clawhub-mini/1.0 (+https://clawhub.ai)";

// ---------------------------------------------------------------------------
// Tiny terminal styling (no dependency on chalk / styleText availability).
// ---------------------------------------------------------------------------
const useColor = process.stdout.isTTY && !process.env.NO_COLOR;
const paint = (code, s) => (useColor ? `[${code}m${s}[0m` : String(s));
const c = {
  bold: (s) => paint("1", s),
  dim: (s) => paint("2", s),
  red: (s) => paint("31", s),
  green: (s) => paint("32", s),
  yellow: (s) => paint("33", s),
  cyan: (s) => paint("36", s),
};

function fail(msg) {
  console.error(`${c.red("error")} ${msg}`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// HTTP helpers. Prefer curl when present (handles proxies transparently, which
// matters in CI/sandboxed environments); otherwise fall back to global fetch.
// ---------------------------------------------------------------------------
let curlChecked = false;
let curlAvailable = false;
function hasCurl() {
  if (curlChecked) return curlAvailable;
  curlChecked = true;
  try {
    execFileSync("curl", ["--version"], { stdio: "ignore" });
    curlAvailable = true;
  } catch {
    curlAvailable = false;
  }
  return curlAvailable;
}

async function httpGetJson(url) {
  if (hasCurl()) {
    const marker = "\n__CLAWHUB_STATUS__";
    let out;
    try {
      out = execFileSync(
        "curl",
        ["-sS", "-A", USER_AGENT, "-H", "Accept: application/json", "-w", `${marker}%{http_code}`, url],
        { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }
      );
    } catch (e) {
      throw new Error(`network request failed: ${e.message}`);
    }
    const idx = out.lastIndexOf(marker);
    const status = idx >= 0 ? Number(out.slice(idx + marker.length).trim()) : 0;
    const body = idx >= 0 ? out.slice(0, idx) : out;
    return { status, json: safeJson(body), raw: body };
  }
  const res = await fetch(url, { headers: { Accept: "application/json", "User-Agent": USER_AGENT } });
  const raw = await res.text();
  return { status: res.status, json: safeJson(raw), raw };
}

async function httpGetBuffer(url, destFile) {
  if (hasCurl()) {
    let status;
    try {
      status = execFileSync(
        "curl",
        ["-sS", "-A", USER_AGENT, "-o", destFile, "-w", "%{http_code}", url],
        { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 }
      ).trim();
    } catch (e) {
      throw new Error(`download failed: ${e.message}`);
    }
    if (Number(status) >= 400) {
      throw new Error(`download failed with HTTP ${status}`);
    }
    return fs.readFileSync(destFile);
  }
  const res = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
  if (!res.ok) throw new Error(`download failed with HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(destFile, buf);
  return buf;
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function registryUrl(registry, pathname, params) {
  const base = registry.endsWith("/") ? registry : `${registry}/`;
  const url = new URL(pathname.replace(/^\//, ""), base);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  return url.toString();
}

// ---------------------------------------------------------------------------
// Slug parsing: `slug`, `@owner/slug`, or `owner/slug`.
// ---------------------------------------------------------------------------
function isSafeSegment(s) {
  return Boolean(s) && !s.includes("/") && !s.includes("\\") && !s.includes("..");
}

function parseSkillRef(raw) {
  const ref = String(raw || "").trim().replace(/^@/, "");
  if (!ref) fail("A skill slug is required. Try: clawhub install <skill-slug>");
  const slash = ref.indexOf("/");
  if (slash === -1) {
    if (!isSafeSegment(ref)) fail(`Invalid slug: ${raw}`);
    return { slug: ref, ownerHandle: undefined };
  }
  const ownerHandle = ref.slice(0, slash);
  const slug = ref.slice(slash + 1);
  if (!isSafeSegment(ownerHandle) || !isSafeSegment(slug)) fail(`Invalid slug: ${raw}`);
  return { slug, ownerHandle };
}

function refLabel(ref) {
  return ref.ownerHandle ? `@${ref.ownerHandle}/${ref.slug}` : ref.slug;
}

function skillTarget(dir, ref) {
  return ref.ownerHandle
    ? path.join(dir, `@${ref.ownerHandle}`, ref.slug)
    : path.join(dir, ref.slug);
}

// ---------------------------------------------------------------------------
// Minimal ZIP extraction (store + deflate) using the central directory.
// ---------------------------------------------------------------------------
function extractZip(buf, targetDir) {
  const EOCD_SIG = 0x06054b50;
  const CEN_SIG = 0x02014b50;

  // Locate End Of Central Directory record by scanning backwards.
  let eocd = -1;
  for (let i = buf.length - 22; i >= 0 && i >= buf.length - 22 - 0xffff; i--) {
    if (buf.readUInt32LE(i) === EOCD_SIG) {
      eocd = i;
      break;
    }
  }
  if (eocd === -1) throw new Error("invalid archive: End Of Central Directory not found");

  const entryCount = buf.readUInt16LE(eocd + 10);
  let ptr = buf.readUInt32LE(eocd + 16); // offset of central directory

  const written = [];
  for (let n = 0; n < entryCount; n++) {
    if (buf.readUInt32LE(ptr) !== CEN_SIG) throw new Error("invalid archive: bad central directory entry");
    const method = buf.readUInt16LE(ptr + 10);
    const compSize = buf.readUInt32LE(ptr + 20);
    const nameLen = buf.readUInt16LE(ptr + 28);
    const extraLen = buf.readUInt16LE(ptr + 30);
    const commentLen = buf.readUInt16LE(ptr + 32);
    const localOffset = buf.readUInt32LE(ptr + 42);
    const name = buf.toString("utf8", ptr + 46, ptr + 46 + nameLen);
    ptr += 46 + nameLen + extraLen + commentLen;

    if (name.endsWith("/")) continue; // directory entry

    // Read local header to find where the file data actually begins.
    if (buf.readUInt32LE(localOffset) !== 0x04034b50) throw new Error("invalid archive: bad local header");
    const lNameLen = buf.readUInt16LE(localOffset + 26);
    const lExtraLen = buf.readUInt16LE(localOffset + 28);
    const dataStart = localOffset + 30 + lNameLen + lExtraLen;
    const compData = buf.subarray(dataStart, dataStart + compSize);

    let content;
    if (method === 0) content = Buffer.from(compData);
    else if (method === 8) content = zlib.inflateRawSync(compData);
    else throw new Error(`unsupported compression method ${method} for ${name}`);

    const safe = sanitizeRelPath(name);
    if (!safe) continue;
    const outPath = path.join(targetDir, safe);
    if (!outPath.startsWith(path.resolve(targetDir) + path.sep) && outPath !== path.resolve(targetDir)) {
      // Extra guard against path traversal.
      continue;
    }
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, content);
    written.push(safe);
  }
  return written;
}

function sanitizeRelPath(rawPath) {
  const normalized = rawPath.replace(/\\/g, "/").replace(/^\/+/, "");
  const parts = normalized.split("/").filter((p) => p && p !== ".");
  if (parts.some((p) => p === "..")) return null;
  return parts.join(path.sep);
}

// ---------------------------------------------------------------------------
// Lockfile + per-skill origin record (compatible with the official layout).
// ---------------------------------------------------------------------------
function readLockfile(workdir) {
  const p = path.join(workdir, DOT_DIR, "lock.json");
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return { version: 1, skills: {} };
  }
}

function writeLockfile(workdir, lock) {
  const p = path.join(workdir, DOT_DIR, "lock.json");
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, `${JSON.stringify(lock, null, 2)}\n`);
}

function writeOrigin(skillFolder, origin) {
  const p = path.join(skillFolder, DOT_DIR, "origin.json");
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, `${JSON.stringify(origin, null, 2)}\n`);
}

function fingerprint(files, targetDir) {
  const parts = files
    .slice()
    .sort()
    .map((rel) => {
      const abs = path.join(targetDir, rel);
      const hash = crypto.createHash("sha256").update(fs.readFileSync(abs)).digest("hex");
      return `${rel.split(path.sep).join("/")}:${hash}`;
    });
  return crypto.createHash("sha256").update(parts.join("\n")).digest("hex");
}

// ---------------------------------------------------------------------------
// SKILL.md frontmatter verification.
// ---------------------------------------------------------------------------
function verifySkillMd(targetDir) {
  const candidates = [path.join(targetDir, "SKILL.md"), path.join(targetDir, "skill.md")];
  const found = candidates.find((f) => fs.existsSync(f));
  if (!found) {
    console.warn(`${c.yellow("warning")} no SKILL.md found in the installed skill.`);
    return;
  }
  const text = fs.readFileSync(found, "utf8");
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) {
    console.warn(`${c.yellow("warning")} SKILL.md has no YAML frontmatter block.`);
    return;
  }
  const hasName = /^\s*name\s*:/m.test(m[1]);
  const hasDesc = /^\s*description\s*:/m.test(m[1]);
  if (!hasName && !hasDesc) {
    console.warn(`${c.yellow("warning")} SKILL.md frontmatter is missing 'name' and 'description'.`);
  }
}

// ---------------------------------------------------------------------------
// Argument parsing.
// ---------------------------------------------------------------------------
function parseArgs(argv) {
  const positionals = [];
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--force" || a === "-f") flags.force = true;
    else if (a === "--yes" || a === "-y") flags.yes = true;
    else if (a === "--help" || a === "-h") flags.help = true;
    else if (a === "--version" || a === "-v") flags.version = argv[++i];
    else if (a === "--dir" || a === "-d") flags.dir = argv[++i];
    else if (a === "--registry") flags.registry = argv[++i];
    else if (a === "--workdir") flags.workdir = argv[++i];
    else if (a.startsWith("--version=")) flags.version = a.slice("--version=".length);
    else if (a.startsWith("--dir=")) flags.dir = a.slice("--dir=".length);
    else if (a.startsWith("--registry=")) flags.registry = a.slice("--registry=".length);
    else if (a.startsWith("--workdir=")) flags.workdir = a.slice("--workdir=".length);
    else positionals.push(a);
  }
  return { positionals, flags };
}

function resolveDir(flags) {
  // Priority: --dir flag, CLAWHUB_INSTALL_DIR env, then a sensible default.
  if (flags.dir) return path.resolve(flags.dir);
  if (process.env.CLAWHUB_INSTALL_DIR) return path.resolve(process.env.CLAWHUB_INSTALL_DIR);
  // Match the official CLI's default of ./skills, but prefer this repo's
  // .codex/skills convention when a .codex directory is present.
  const workdir = resolveWorkdir(flags);
  if (fs.existsSync(path.join(workdir, ".codex"))) return path.join(workdir, ".codex", "skills");
  return path.join(workdir, "skills");
}

function resolveWorkdir(flags) {
  if (flags.workdir) return path.resolve(flags.workdir);
  if (process.env.CLAWHUB_WORKDIR) return path.resolve(process.env.CLAWHUB_WORKDIR);
  return process.cwd();
}

function resolveRegistry(flags) {
  return (flags.registry || process.env.CLAWHUB_REGISTRY || DEFAULT_REGISTRY).replace(/\/+$/, "");
}

// ---------------------------------------------------------------------------
// The install command.
// ---------------------------------------------------------------------------
async function cmdInstall(rawSlug, flags) {
  if (!rawSlug) fail("A skill slug is required. Try: clawhub install <skill-slug>");
  const requested = parseSkillRef(rawSlug);
  const registry = resolveRegistry(flags);
  const workdir = resolveWorkdir(flags);
  const dir = resolveDir(flags);
  fs.mkdirSync(dir, { recursive: true });

  // 1. Fetch skill metadata (also surfaces moderation + ambiguity).
  process.stderr.write(`${c.cyan("→")} Resolving ${c.bold(refLabel(requested))} …\n`);
  const metaRes = await httpGetJson(
    registryUrl(registry, `/api/v1/skills/${encodeURIComponent(requested.slug)}`, {
      ownerHandle: requested.ownerHandle,
    })
  );

  if (metaRes.json && metaRes.json.code === "AMBIGUOUS_SKILL_SLUG") {
    printAmbiguous(metaRes.json);
    process.exit(1);
  }
  if (metaRes.status === 404 || (metaRes.json && metaRes.json.code === "NOT_FOUND")) {
    fail(`Skill not found: ${refLabel(requested)}`);
  }
  if (metaRes.status >= 400 || !metaRes.json) {
    fail(`Registry error (HTTP ${metaRes.status}) resolving ${refLabel(requested)}`);
  }

  const meta = metaRes.json;
  const ownerHandle = requested.ownerHandle || (meta.owner && meta.owner.handle);
  const remoteSlug = (meta.skill && meta.skill.slug) || requested.slug;
  const resolvedRef = { slug: requested.slug, ownerHandle };

  // 2. Moderation checks (mirrors the official client).
  const moderation = meta.moderation || {};
  if (moderation.isMalwareBlocked) {
    fail(`Blocked: "${refLabel(resolvedRef)}" is flagged as malware and cannot be installed.`);
  }
  if (moderation.isSuspicious && !flags.force) {
    console.warn(
      `\n${c.yellow("⚠")}  "${refLabel(resolvedRef)}" is flagged for ClawHub security review.\n` +
        `   It may contain risky patterns. Review the code, then re-run with --force to install.\n`
    );
    fail("Refusing to install a flagged skill without --force.");
  }

  // 3. Guard against clobbering an existing install.
  const target = skillTarget(dir, resolvedRef);
  if (fs.existsSync(target) && !flags.force) {
    fail(`Already installed: ${path.relative(process.cwd(), target)} (use --force to reinstall)`);
  }

  // 4. Resolve the version / download URL.
  let version = flags.version || (meta.latestVersion && meta.latestVersion.version) || null;
  let downloadUrl = null;

  const installRes = await httpGetJson(
    registryUrl(registry, `/api/v1/skills/${encodeURIComponent(remoteSlug)}/install`, { ownerHandle })
  );
  if (installRes.json && installRes.json.ok && installRes.json.installKind === "archive" && installRes.json.archive) {
    if (!flags.version) version = installRes.json.archive.version || version;
    downloadUrl = installRes.json.archive.downloadUrl || null;
  } else if (installRes.json && installRes.json.installKind === "github") {
    fail(
      `"${refLabel(resolvedRef)}" installs from GitHub source, which this minimal client does not support. ` +
        `Use the official CLI: npx clawhub install ${refLabel(resolvedRef)}`
    );
  }

  if (!version) fail("Could not resolve a version to install.");
  if (!downloadUrl) {
    downloadUrl = registryUrl(registry, "/api/v1/download", { slug: remoteSlug, ownerHandle, version });
  }

  // 5. Download + extract atomically (stage in a temp dir, then swap).
  process.stderr.write(`${c.cyan("→")} Downloading ${c.bold(refLabel(resolvedRef))} ${c.dim("v" + version)} …\n`);
  const parent = path.dirname(target);
  fs.mkdirSync(parent, { recursive: true });
  const stage = fs.mkdtempSync(path.join(parent, `.${path.basename(target)}.tmp-`));
  const tmpZip = path.join(stage, "__archive.zip");
  let written;
  try {
    const buf = await httpGetBuffer(downloadUrl, tmpZip);
    fs.rmSync(tmpZip, { force: true });
    written = extractZip(buf, stage);
    if (fs.existsSync(target)) fs.rmSync(target, { recursive: true, force: true });
    fs.renameSync(stage, target);
  } catch (e) {
    fs.rmSync(stage, { recursive: true, force: true });
    fail(`Installation failed: ${e.message}`);
  }

  // 6. Verify SKILL.md frontmatter.
  verifySkillMd(target);

  // 7. Record origin + lockfile.
  const installedAt = Date.now();
  const fp = written.length ? fingerprint(written, target) : undefined;
  writeOrigin(target, {
    version: 1,
    registry,
    slug: remoteSlug,
    ...(ownerHandle ? { ownerHandle } : {}),
    installedVersion: version,
    installedAt,
    ...(fp ? { fingerprint: fp } : {}),
  });

  const lock = readLockfile(workdir);
  const lockKey = ownerHandle ? `@${ownerHandle}/${remoteSlug}` : remoteSlug;
  lock.skills = lock.skills || {};
  lock.skills[lockKey] = { version, installedAt, ...(ownerHandle ? { ownerHandle } : {}) };
  writeLockfile(workdir, lock);

  console.log(
    `${c.green("✓ Installed")} ${c.bold(refLabel(resolvedRef))} ${c.dim("v" + version)} → ${c.cyan(
      path.relative(process.cwd(), target) || target
    )}`
  );
}

function printAmbiguous(res) {
  console.error(`${c.yellow("Multiple skills share the slug")} "${res.slug}". Pick one:`);
  for (const m of res.matches || []) {
    console.error(`  ${c.bold(m.ref || `@${m.ownerHandle}/${m.slug}`)}`);
    if (m.url) console.error(`    ${c.dim(m.url)}`);
  }
  console.error(`\nRe-run with the fully qualified slug, e.g. clawhub install ${(res.matches && res.matches[0] && res.matches[0].ref) || "@owner/" + res.slug}`);
}

function printHelp() {
  console.log(`${c.bold("clawhub")} — install agent skills from the ClawHub registry

${c.bold("Usage")}
  clawhub install <skill-slug> [options]

${c.bold("Arguments")}
  <skill-slug>          A bare slug (e.g. ${c.cyan("github")}) or a fully
                        qualified ${c.cyan("@owner/slug")} (e.g. ${c.cyan("@steipete/github")}).

${c.bold("Options")}
  -v, --version <ver>   Install a specific version instead of the latest.
  -f, --force           Reinstall over an existing copy / install a flagged skill.
  -d, --dir <path>      Directory to install into (default: ${c.cyan(".codex/skills")} in
                        this repo, otherwise ${c.cyan("./skills")}).
      --workdir <path>  Directory that holds the ${c.cyan(".clawhub/lock.json")} lockfile
                        (default: current directory).
      --registry <url>  Registry base URL (default: ${c.cyan(DEFAULT_REGISTRY)}).
  -y, --yes             Assume yes for prompts (non-interactive).
  -h, --help            Show this help.

${c.bold("Environment")}
  CLAWHUB_REGISTRY      Overrides the registry base URL.
  CLAWHUB_INSTALL_DIR   Overrides the install directory.
  CLAWHUB_WORKDIR       Overrides the lockfile working directory.

${c.bold("Examples")}
  clawhub install @steipete/github
  clawhub install @steipete/github --version 1.0.0
  clawhub install @dbalve/fast-io --dir .codex/skills --force
`);
}

async function main() {
  const argv = process.argv.slice(2);
  const { positionals, flags } = parseArgs(argv);
  const command = positionals[0];

  if (flags.help || !command) {
    printHelp();
    process.exit(command ? 0 : flags.help ? 0 : 1);
  }

  if (command === "install" || command === "i" || command === "add") {
    await cmdInstall(positionals[1], flags);
    return;
  }

  fail(`Unknown command: ${command}\nRun 'clawhub --help' for usage.`);
}

main().catch((e) => fail(e && e.message ? e.message : String(e)));
