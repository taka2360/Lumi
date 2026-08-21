/**
 * Generate a third-party license list for the distribution from **dependency graphs across three ecosystems**
 *
 *   node scripts/generate-oss-notice.mjs
 *   -> stage/src/credits/third-party.generated.json
 *
 * roadmap Phase 0 "Generate OSS notice including transitive dependencies"
 * Hand-written lists become outdated. **Ensure notices update when dependencies are added**
 *
 * Only inspecting components that are actually included in the distributable:
 *   - Rust  : `cargo tree -e normal` (build/dev dependencies not included in exe)
 *   - Python: `uv export --no-dev` (runtime dependencies bundled by PyInstaller)
 *   - JS    : `pnpm licenses list --prod` (included in Stage dist)
 *
 * **Bundled items that do not appear in dependency graphs** (CPython runtime, PortAudio,
 * PyInstaller bootloader, etc.) are manually specified in MANUAL below
 */

import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = join(ROOT, "stage", "src", "credits", "third-party.generated.json");

/** Deny-list to preserve the Core = MIT boundary. **Generation fails if any match** */
const REFUSED = [/\bGPL/i, /\bAGPL/i, /\bSSPL/i, /\bproprietary\b/i, /\bunlicensed\b/i];

/** Items bundled into the distribution but absent from dependency graphs */
const MANUAL = [
	{
		name: "CPython",
		version: "3.12.11",
		license: "PSF-2.0",
		note: "PyInstaller が固めた Python 本体（python312.dll と標準ライブラリ）",
	},
	{
		name: "SQLite",
		version: "3.49.1",
		license: "Public Domain",
		note: "CPython に同梱（FTS5 を含む）",
	},
	{
		name: "PortAudio",
		version: "V19.7.0",
		license: "MIT",
		note: "sounddevice が同梱するバイナリ。**ASIO 版は同梱しない**（Steinberg の SDK は非 OSS）",
	},
	{
		name: "Silero VAD",
		version: "v6 (ONNX)",
		license: "MIT",
		note: "Copyright (c) 2020-present Silero Team。**faster-whisper が同梱する ONNX モデル**であり、依存宣言には現れないため手で足している（docs/licensing.md §4.6）",
	},
	{
		name: "PyInstaller bootloader",
		version: "6.22.0",
		license: "GPL-2.0-or-later WITH Bootloader-exception",
		note: "**固めた実行体に埋め込まれる。** 例外条項により、これを使って作った実行体に GPL は伝播しない",
	},
];

/** Items listed in MANUAL are exempted from the deny-list (only items with verified exceptions are placed here) */
function run(command, args, cwd) {
	return execFileSync(command, args, {
		cwd,
		encoding: "utf8",
		maxBuffer: 64 * 1024 * 1024,
		shell: process.platform === "win32",
	});
}

function rustPackages() {
	const shell = join(ROOT, "shell", "src-tauri");
	// Only packages actually linked (normal dependencies); build-dependencies are not included in the exe
	const tree = run("cargo", ["tree", "-e", "normal", "--prefix", "none", "--no-dedupe"], shell);
	const wanted = new Set();
	for (const line of tree.split("\n")) {
		const match = line.trim().match(/^([A-Za-z0-9_.-]+)\s+v([^\s]+)/);
		if (match) wanted.add(`${match[1]}@${match[2]}`);
	}

	const metadata = JSON.parse(run("cargo", ["metadata", "--format-version", "1"], shell));
	const packages = [];
	for (const pkg of metadata.packages) {
		const key = `${pkg.name}@${pkg.version}`;
		if (!wanted.has(key) || pkg.name === "lumi-shell") continue;
		packages.push({
			name: pkg.name,
			version: pkg.version,
			license: pkg.license ?? (pkg.license_file ? "(Bundled LICENSE file)" : "Unknown"),
		});
	}
	return packages;
}

function pythonPackages() {
	const core = join(ROOT, "core");
	const exported = run(
		"uv",
		["export", "--no-dev", "--no-emit-project", "--format", "requirements.txt"],
		core,
	);
	const sitePackages = join(core, ".venv", "Lib", "site-packages");
	const distInfos = readdirSync(sitePackages).filter((name) => name.endsWith(".dist-info"));

	const packages = [];
	for (const line of exported.split("\n")) {
		const match = line.trim().match(/^([A-Za-z0-9_.-]+)==([^\s;\\]+)/);
		if (!match) continue;
		const [, name, version] = match;
		packages.push({ name, version, license: pythonLicense(sitePackages, distInfos, name, version) });
	}
	return packages;
}

/** Extract from dist-info METADATA in order: License-Expression -> License -> Classifier */
function pythonLicense(sitePackages, distInfos, name, version) {
	const normalized = name.replace(/[-_.]+/g, "_").toLowerCase();
	const found = distInfos.find((dir) => {
		const base = dir.slice(0, -".dist-info".length);
		const [distName, distVersion] = base.split("-");
		return distName.replace(/[-_.]+/g, "_").toLowerCase() === normalized && distVersion === version;
	});
	if (!found) return "Unknown";

	const metadata = readFileSync(join(sitePackages, found, "METADATA"), "utf8");
	const expression = metadata.match(/^License-Expression:\s*(.+)$/m);
	if (expression) return expression[1].trim();
	const license = metadata.match(/^License:\s*(.+)$/m);
	if (license && license[1].trim().length < 60) return license[1].trim();
	const classifier = metadata.match(/^Classifier:\s*License\s*::\s*(?:OSI Approved\s*::\s*)?(.+)$/m);
	return classifier ? classifier[1].trim() : "Unknown";
}

function jsPackages() {
	const stage = join(ROOT, "stage");
	const listed = JSON.parse(run("pnpm", ["licenses", "list", "--prod", "--json"], stage));
	const packages = [];
	for (const [license, entries] of Object.entries(listed)) {
		for (const entry of entries) {
			for (const version of entry.versions ?? []) {
				packages.push({ name: entry.name, version, license });
			}
		}
	}
	return packages;
}

function refuse(packages, ecosystem) {
	const bad = packages.filter((pkg) => REFUSED.some((pattern) => pattern.test(pkg.license)));
	if (bad.length > 0) {
		const listed = bad.map((pkg) => `  ${pkg.name} ${pkg.version} — ${pkg.license}`).join("\n");
		throw new Error(
			`${ecosystem} contains dependencies with unacceptable licenses (Core = MIT boundary):\n${listed}`,
		);
	}
}

const ecosystems = [
	{ name: "Lumi Shell（Rust / exe にリンクされるもの）", packages: rustPackages() },
	{ name: "Lumi Core（Python / サイドカーに固められるもの）", packages: pythonPackages() },
	{ name: "Stage（JavaScript / 配布物に入るもの）", packages: jsPackages() },
];

for (const ecosystem of ecosystems) {
	ecosystem.packages.sort((a, b) => a.name.localeCompare(b.name));
	refuse(ecosystem.packages, ecosystem.name);
}
ecosystems.push({
	name: "依存グラフに現れないが配布されるもの",
	packages: MANUAL,
});

const total = ecosystems.reduce((sum, ecosystem) => sum + ecosystem.packages.length, 0);
// **Do not write generation timestamp.** Diffs on unchanged content make real changes hard to notice
writeFileSync(OUTPUT, `${JSON.stringify({ total, ecosystems }, null, 2)}\n`, "utf8");
console.log(`Generated ${OUTPUT} (${total} items)`);
