#!/usr/bin/env node
/**
 * Render N segment compositions with ONE bundle and ONE browser.
 *
 * The old pipeline shelled out to `npx remotion render <KEY>` once per segment,
 * paying npx resolution + a full rspack bundle + a Chrome launch every time —
 * ~25-40s of fixed overhead per composition (H5 is 3.0s of video and took 46s
 * wall). Eight segments meant 200-320s of pure startup. Here the bundle is
 * produced once (and cached by the build's `bundle` node, so it is usually
 * skipped entirely) and the browser is reused across compositions.
 *
 * Renders stay SERIAL across compositions on purpose: they contend for the same
 * browser and encoder. Parallelism belongs INSIDE a render, via `concurrency`.
 *
 * Usage:
 *   node render_segments.mjs --bundle-only --out <dir>
 *   echo '{"segments":[{"key":"BODY","out":"/abs/path.mp4"}],"crf":14,
 *          "bundleDir":"/abs/work/bundle","concurrency":0}' | node render_segments.mjs
 */
import { readFileSync, renameSync, mkdirSync, existsSync } from "node:fs";
import { dirname } from "node:path";

const argv = process.argv.slice(2);
const flag = (n) => argv.includes(n);
const val = (n, d) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : d; };

const { bundle } = await import("@remotion/bundler");
const { openBrowser, selectComposition, renderMedia, ensureBrowser } =
  await import("@remotion/renderer");

const entry = existsSync("src/index.ts") ? "src/index.ts"
            : existsSync("src/index.tsx") ? "src/index.tsx"
            : "src/index.ts";

if (flag("--bundle-only")) {
  const outDir = val("--out", "work/bundle.part");
  mkdirSync(outDir, { recursive: true });
  const url = await bundle({ entryPoint: entry, outDir });
  console.log(JSON.stringify({ ok: true, bundle: url }));
  process.exit(0);
}

const cfg = JSON.parse(readFileSync(0, "utf8"));
const segments = cfg.segments || [];
if (segments.length === 0) { console.log("nothing to render"); process.exit(0); }

await ensureBrowser();

// Reuse the cached bundle when the build says it is current; otherwise make one.
let serveUrl = cfg.bundleDir;
if (!serveUrl || !existsSync(serveUrl)) {
  serveUrl = await bundle({ entryPoint: entry, outDir: cfg.bundleDir || "work/bundle" });
}

const browser = await openBrowser("chrome");
let failed = null;
try {
  for (const seg of segments) {
    const t0 = Date.now();
    const composition = await selectComposition({
      serveUrl, id: seg.key, puppeteerInstance: browser,
    });
    const part = seg.out + ".part";
    mkdirSync(dirname(seg.out), { recursive: true });
    let lastPct = -1;
    await renderMedia({
      composition, serveUrl, puppeteerInstance: browser,
      codec: "h264",
      crf: cfg.crf ?? 14,
      concurrency: cfg.concurrency && cfg.concurrency > 0 ? cfg.concurrency : null,
      outputLocation: part,
      onProgress: ({ progress }) => {
        const pct = Math.round(progress * 100);
        if (pct >= lastPct + 20) { lastPct = pct; process.stderr.write(`    ${seg.key} ${pct}%\n`); }
      },
    });
    renameSync(part, seg.out);   // atomic: never adopt a half-written render
    console.log(JSON.stringify({ seg: seg.key, wall_s: (Date.now() - t0) / 1000 }));
  }
} catch (e) {
  failed = e;
} finally {
  await browser.close();
}
if (failed) { console.error(String(failed && failed.stack || failed)); process.exit(1); }
