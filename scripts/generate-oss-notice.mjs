/**
 * 配布物に入るサードパーティの一覧を、**3つのエコシステムの依存グラフから生成する**。
 *
 *   node scripts/generate-oss-notice.mjs
 *   → stage/src/credits/third-party.generated.json
 *
 * roadmap Phase 0「推移的依存を含む OSS 通知の生成」。
 * 手で書いた一覧は必ず古くなる。**依存を足したら通知も変わる**ようにしておく。
 *
 * 見ているのは「実際に配布物へ入るもの」だけ:
 *   - Rust  : `cargo tree -e normal`（build/dev 依存は exe に入らない）
 *   - Python: `uv export --no-dev`（PyInstaller が固める実行時依存）
 *   - JS    : `pnpm licenses list --prod`（Stage の dist に入るもの）
 *
 * **依存グラフに出てこないのに配布されるもの**（CPython 本体、PortAudio、
 * PyInstaller の bootloader など）は下の MANUAL に手で書く。ここが抜けやすい。
 */

import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = join(ROOT, "stage", "src", "credits", "third-party.generated.json");

/** Core = MIT の境界を守るための拒否リスト。**これに当たったら生成を失敗させる。** */
const REFUSED = [/\bGPL/i, /\bAGPL/i, /\bSSPL/i, /\bproprietary\b/i, /\bunlicensed\b/i];

/** 依存グラフからは見えないが、配布物に入るもの。 */
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
		name: "PyInstaller bootloader",
		version: "6.22.0",
		license: "GPL-2.0-or-later WITH Bootloader-exception",
		note: "**固めた実行体に埋め込まれる。** 例外条項により、これを使って作った実行体に GPL は伝播しない",
	},
];

/** MANUAL に書いたものは拒否リストの対象外にする（例外条項を確認済みのものだけを置く）。 */
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
	// 実際にリンクされるもの（normal 依存）だけ。build-dependencies は exe に入らない。
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
			license: pkg.license ?? (pkg.license_file ? "（LICENSE ファイル同梱）" : "不明"),
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

/** dist-info の METADATA から拾う。`License-Expression` → `License` → 分類子の順。 */
function pythonLicense(sitePackages, distInfos, name, version) {
	const normalized = name.replace(/[-_.]+/g, "_").toLowerCase();
	const found = distInfos.find((dir) => {
		const base = dir.slice(0, -".dist-info".length);
		const [distName, distVersion] = base.split("-");
		return distName.replace(/[-_.]+/g, "_").toLowerCase() === normalized && distVersion === version;
	});
	if (!found) return "不明";

	const metadata = readFileSync(join(sitePackages, found, "METADATA"), "utf8");
	const expression = metadata.match(/^License-Expression:\s*(.+)$/m);
	if (expression) return expression[1].trim();
	const license = metadata.match(/^License:\s*(.+)$/m);
	if (license && license[1].trim().length < 60) return license[1].trim();
	const classifier = metadata.match(/^Classifier:\s*License\s*::\s*(?:OSI Approved\s*::\s*)?(.+)$/m);
	return classifier ? classifier[1].trim() : "不明";
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
			`${ecosystem} に受け入れられないライセンスの依存がある（Core = MIT の境界）:\n${listed}`,
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
// **生成日時を書かない。** 中身が変わらないのに差分が出ると、変更に気づけなくなる。
writeFileSync(OUTPUT, `${JSON.stringify({ total, ecosystems }, null, 2)}\n`, "utf8");
console.log(`${OUTPUT} を生成した（${total} 件）`);
