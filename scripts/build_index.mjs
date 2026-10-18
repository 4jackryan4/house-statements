// Build the Pagefind full-text search indexes from build/records/ into site/pagefind/<name>/.
// One index for everything needs more memory than a GitHub runner has, so build_site splits the
// records into a "recent" index plus one per older quarter (newest first in indexes.json). Older
// quarters rarely change: an index whose records match the copy in build/pagefind-cache (restored
// by the workflow from the previous run) is reused instead of rebuilt.
import { spawnSync } from "node:child_process";
import { createReadStream, cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createInterface } from "node:readline";

const CACHE = "build/pagefind-cache";

if (process.argv[2]) {
  // Child process: build one index, so its memory is freed before the next one starts.
  const name = process.argv[2];
  const pagefind = await import("pagefind");
  const { index, errors } = await pagefind.createIndex({});
  if (errors.length) throw new Error(errors.join("\n"));
  const lines = createInterface({ input: createReadStream(`build/records/${name}.jsonl`), crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    const res = await index.addCustomRecord(JSON.parse(line));
    if (res.errors.length) throw new Error(res.errors.join("\n"));
  }
  const out = await index.writeFiles({ outputPath: `site/pagefind/${name}` });
  if (out.errors.length) throw new Error(out.errors.join("\n"));
  await pagefind.close();
} else {
  const version = JSON.parse(readFileSync("node_modules/pagefind/package.json", "utf8")).version;
  const indexes = JSON.parse(readFileSync("build/records/indexes.json", "utf8"));
  rmSync("site/pagefind", { recursive: true, force: true });
  mkdirSync("site/pagefind", { recursive: true });
  let built = 0;
  for (const { name, count, hash } of indexes) {
    const key = `${version} ${hash}`;
    const cached = `${CACHE}/${name}`;
    if (existsSync(`${cached}/.key`) && readFileSync(`${cached}/.key`, "utf8") === key) {
      cpSync(cached, `site/pagefind/${name}`, { recursive: true });
      continue;
    }
    const res = spawnSync(process.execPath, [process.argv[1], name], { stdio: "inherit" });
    if (res.status !== 0) throw new Error(`build_index: ${name} failed (${res.signal || res.status})`);
    built++;
    if (name !== "recent") {  // the recent index changes every run, so it isn't kept
      rmSync(cached, { recursive: true, force: true });
      cpSync(`site/pagefind/${name}`, cached, { recursive: true });
      writeFileSync(`${cached}/.key`, key);
    }
    console.log(`build_index: ${name}: ${count} statements`);
  }
  // Drop cached quarters that no longer exist, so the cache doesn't grow.
  if (existsSync(CACHE)) {
    const names = new Set(indexes.map((i) => i.name));
    for (const d of readdirSync(CACHE)) if (!names.has(d)) rmSync(`${CACHE}/${d}`, { recursive: true });
  }
  // What the workflow names the cache by: it's saved again only when an older quarter changed.
  writeFileSync("build/pagefind-key.txt", indexes.filter((i) => i.name !== "recent").map((i) => `${i.name} ${version} ${i.hash}\n`).join(""));
  writeFileSync("site/pagefind/indexes.json", JSON.stringify(indexes.map((i) => i.name)));
  const total = indexes.reduce((n, i) => n + i.count, 0);
  console.log(`build_index: ${total} statements in ${indexes.length} indexes (${built} rebuilt, ${indexes.length - built} reused)`);
}
