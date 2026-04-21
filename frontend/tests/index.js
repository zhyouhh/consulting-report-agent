import { readdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const testsDir = dirname(fileURLToPath(import.meta.url));
const testFiles = (await readdir(testsDir))
  .filter((fileName) => fileName.endsWith(".test.mjs"))
  .sort();

for (const testFile of testFiles) {
  await import(pathToFileURL(join(testsDir, testFile)).href);
}
