import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let count = 0;
function walk(folder) {
  for (const item of readdirSync(folder, { withFileTypes: true })) {
    const file = resolve(folder, item.name);
    if (item.isDirectory()) walk(file);
    else if (/\.(mjs|js)$/.test(file)) {
      const result = spawnSync(process.execPath, ['--check', file], { stdio: 'inherit' });
      if (result.status !== 0) process.exit(result.status || 1);
      for (const match of readFileSync(file, 'utf8').matchAll(/(?:from\s*|import\s*)['"](\.[^'"]+)['"]/g)) {
        if (!existsSync(resolve(dirname(file), match[1].split(/[?#]/)[0]))) throw new Error(`Unresolved relative import in ${file}`);
      }
      count++;
    }
  }
}
walk(resolve(root, 'frontend/static'));
walk(resolve(root, 'backend/harness'));
const catalog = JSON.parse(readFileSync(resolve(root, 'backend/tools/registry.json'), 'utf8'));
if (new Set(catalog.tools.map(tool => tool.name)).size !== catalog.tools.length) throw new Error('Duplicate tool name');
console.log(`Checked ${count} JavaScript modules and the shared tool registry`);
