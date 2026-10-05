// Build the Pagefind full-text search index from build/records.jsonl into site/pagefind.
import { createReadStream } from "node:fs";
import { createInterface } from "node:readline";
import * as pagefind from "pagefind";

const { index, errors } = await pagefind.createIndex({});
if (errors.length) throw new Error(errors.join("\n"));

let count = 0;
const lines = createInterface({ input: createReadStream("build/records.jsonl"), crlfDelay: Infinity });
for await (const line of lines) {
  if (!line.trim()) continue;
  const res = await index.addCustomRecord(JSON.parse(line));
  if (res.errors.length) throw new Error(res.errors.join("\n"));
  count++;
}

const out = await index.writeFiles({ outputPath: "site/pagefind" });
if (out.errors.length) throw new Error(out.errors.join("\n"));
await pagefind.close();
console.log(`build_index: indexed ${count} statements`);
