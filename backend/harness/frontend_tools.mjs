import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { spawn } from 'node:child_process';
import path from 'node:path';

export const name = 'duplex-frontend-business-tools';
export const inject = ['tools'];

export function apply(ctx, config) {
  const registry = JSON.parse(readFileSync(path.join(config.bundleRoot, '../tools/registry.json'), 'utf8'));
  // Owner tools (backend/tools/custom/*.json); business_tools.py validates them through the same catalog.
  const customDir = config.customTools || path.join(config.bundleRoot, '../tools/custom');
  const custom = existsSync(customDir) ? readdirSync(customDir).filter(file => file.endsWith('.json')).sort()
    .map(file => ({ ...JSON.parse(readFileSync(path.join(customDir, file), 'utf8')), custom: true })) : [];
  for (const schema of [...registry.tools, ...custom]) {
    // Deterministic transport names; canonical tool/argument schemas are never
    // rewritten. Dot names remain in the description and structured result.
    const alias = 'front__' + schema.name.replaceAll('.', '__');
    const personal = schema.requires_session_authorization === true || (schema.custom === true && schema.read_only === false);
    ctx.tools.register({
      name: alias,
      description: schema.description + ' Canonical frontend tool: ' + schema.name + '. ' +
        (personal ? (config.personalContext ? 'Use only the existing owner-scoped personal authorization for this request.' : 'Requires the user\'s existing personal authorization and a connected owner-scoped store. This deployment cannot access it until that authorization is connected.') :
          schema.custom ? 'Owner-supplied tool; its result comes from the owner\'s endpoint.' :
          schema.source === 'live' ? 'Use the actual adapter and its source label.' : 'Check the returned source/is_fixture label; simulated results are not verified public facts.'),
      parameters: schema.parameters,
      timeoutMs: 45000,
      output: { schema: { type: 'object', additionalProperties: true },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      execute(args, exec) {
        return new Promise((resolve, reject) => {
          if (exec.signal.aborted) return reject(new Error('Cancelled'));
          const child = spawn(config.python, ['-S', path.join(config.bundleRoot, 'business_tools.py')],
            { stdio: ['pipe', 'pipe', 'ignore'], env: { ...process.env, CUDA_VISIBLE_DEVICES: '', DUPLEX_CUSTOM_TOOLS: customDir } });
          let output = '', finished = false;
          const finish = (error, value) => {
            if (finished) return; finished = true;
            clearTimeout(timer); exec.signal.removeEventListener('abort', cancel);
            if (error) reject(error); else resolve(value);
          };
          const cancel = () => { child.kill('SIGTERM'); finish(new Error('Cancelled')); };
          const timer = setTimeout(() => { child.kill('SIGKILL'); finish(new Error('Frontend adapter timed out')); }, 40000);
          exec.signal.addEventListener('abort', cancel, { once: true });
          child.on('error', () => finish(new Error('Frontend adapter could not start')));
          child.stdout.on('data', chunk => { output += chunk; if (output.length > 256000) cancel(); });
          child.on('close', code => {
            try {
              const value = JSON.parse(output);
              if (code !== 0 || value.error) return finish(new Error(value.error?.message || 'Frontend adapter failed'));
              finish(null, value);
            } catch { finish(new Error('Invalid frontend adapter response')); }
          });
          child.stdin.end(JSON.stringify({ name: schema.name, arguments: args, personal_context: config.personalContext||null }));
        });
      },
    });
  }
}
