import { promises as fs } from 'node:fs';
import path from 'node:path';

export const name = 'duplex-readonly-workspace';
export const inject = ['tools'];

export function apply(ctx, config) {
  const root = path.resolve(config.workspaceRoot);
  const resolveFile = async value => {
    if (typeof value !== 'string' || !value || value.length > 240) throw new Error('Invalid relative file path');
    const file = await fs.realpath(path.resolve(root, value));
    if (!file.startsWith(root + path.sep)) throw new Error('File is outside the selected workspace');
    const stat = await fs.stat(file);
    if (!stat.isFile() || stat.size > 2 * 1024 * 1024) throw new Error('Not a bounded text file');
    return file;
  };
  const add = (name, description, properties, required, execute) => ctx.tools.register({
    name, description,
    parameters: { type: 'object', properties, required, additionalProperties: false },
    output: { schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    async execute(args, exec) {
      if (exec.signal.aborted) throw new Error('Cancelled');
      const result = await execute(args, exec);
      if (exec.signal.aborted) throw new Error('Cancelled');
      return result;
    },
  });
  add('workspace_list', 'List the readable reference files in the selected demonstration workspace.', {}, [], async () => ({
    source: 'local_reference_workspace',
    files: (await fs.readdir(root, { withFileTypes: true })).filter(f => f.isFile()).map(f => f.name).sort().slice(0, 128),
  }));
  add('workspace_read', 'Read one reference document. Facts are local reference data, not live external facts.', {
    path: { type: 'string', description: 'Relative path returned by workspace_list or workspace_search.' },
  }, ['path'], async args => ({ source: 'local_reference_workspace', path: args.path,
    text: (await fs.readFile(await resolveFile(args.path), 'utf8')).slice(0, 30000) }));
  add('workspace_search', 'Find reference files containing the requested words, then use workspace_read for evidence.', {
    query: { type: 'string', minLength: 1, maxLength: 200 },
  }, ['query'], async args => {
    const words = args.query.toLowerCase().split(/[\s,，。;；]+/u).filter(Boolean);
    const matches = [];
    for (const entry of (await fs.readdir(root, { withFileTypes: true })).slice(0, 128)) {
      if (!entry.isFile()) continue;
      const file = await resolveFile(entry.name);
      const text = await fs.readFile(file, 'utf8');
      const score = words.filter(w => text.toLowerCase().includes(w) || entry.name.toLowerCase().includes(w)).length;
      if (score) matches.push({ path: entry.name, matched_words: score, excerpt: text.slice(0, 600) });
    }
    return { source: 'local_reference_workspace', matches: matches.sort((a, b) => b.matched_words - a.matched_words).slice(0, 8) };
  });
  add('math_arithmetic', 'Compute a precise arithmetic operation on two supplied numbers. Never invent missing inputs.', {
    a: { type: 'number' }, b: { type: 'number' }, operation: { type: 'string', enum: ['add', 'subtract', 'multiply', 'divide'] },
  }, ['a', 'b', 'operation'], async args => {
    if (!Number.isFinite(args.a) || !Number.isFinite(args.b)) throw new Error('Finite numeric inputs required');
    if (args.operation === 'divide' && args.b === 0) throw new Error('Division by zero');
    const value = ({ add: () => args.a + args.b, subtract: () => args.a - args.b,
      multiply: () => args.a * args.b, divide: () => args.a / args.b })[args.operation]?.();
    if (!Number.isFinite(value)) throw new Error('Invalid arithmetic operation');
    return { source: 'computed', value };
  });
  add('time_convert', 'Convert an explicitly supplied ISO8601 instant into an IANA timezone.', {
    instant: { type: 'string' }, timezone: { type: 'string' },
  }, ['instant', 'timezone'], async args => {
    if (!/(?:Z|[+-]\d\d:\d\d)$/.test(args.instant)) throw new Error('Instant must include its timezone offset');
    const date = new Date(args.instant);
    if (!Number.isFinite(date.getTime())) throw new Error('Invalid instant');
    const local = new Intl.DateTimeFormat('sv-SE', { timeZone: args.timezone,
      dateStyle: 'short', timeStyle: 'medium', hour12: false }).format(date);
    return { source: 'computed', instant: date.toISOString(), timezone: args.timezone, local };
  });
  add('unit_convert', 'Convert a supplied length or mass between supported units.', {
    value: { type: 'number' }, from_unit: { type: 'string', enum: ['m', 'km', 'cm', 'kg', 'g'] },
    to_unit: { type: 'string', enum: ['m', 'km', 'cm', 'kg', 'g'] },
  }, ['value', 'from_unit', 'to_unit'], async args => {
    const units = { m: ['length', 1], km: ['length', 1000], cm: ['length', .01], kg: ['mass', 1], g: ['mass', .001] };
    const from = units[args.from_unit], to = units[args.to_unit];
    if (!from || !to || from[0] !== to[0] || !Number.isFinite(args.value)) throw new Error('Incompatible units');
    return { source: 'computed', value: args.value * from[1] / to[1], unit: args.to_unit };
  });
}
