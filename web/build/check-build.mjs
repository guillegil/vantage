// Checks the built client before it is packaged: the page must run under
// vantage's page policy, and every file must be one vantage serves with a
// known media type. Exits 1 and names each problem.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, process.argv[2] ?? '../../packages/vantage/src/vantage/service/client');

// The suffixes service/web.py maps to a media type; keep the two lists equal.
const SERVED = new Set(['.html', '.js', '.css', '.woff2', '.svg', '.txt']);

const problems = [];

function walk(dir) {
  const files = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) files.push(...walk(path));
    else files.push(path);
  }
  return files;
}

let files = [];
try {
  files = walk(out);
} catch {
  problems.push(`${out} does not exist: run the build first`);
}

const rel = (path) => relative(out, path);

for (const file of files) {
  const suffix = extname(file);
  if (suffix === '.map') problems.push(`${rel(file)}: a source map is not shipped`);
  else if (!SERVED.has(suffix))
    problems.push(`${rel(file)}: ${suffix || 'no suffix'} is not a served type`);
}

const index = join(out, 'index.html');
if (files.length && !files.includes(index)) problems.push('index.html is missing');

if (files.includes(index)) {
  const html = readFileSync(index, 'utf8');
  for (const tag of html.matchAll(/<script\b[^>]*>/gi)) {
    if (!/\ssrc=/i.test(tag[0])) problems.push(`index.html: an inline script: ${tag[0]}`);
  }
  if (/<style\b/i.test(html)) problems.push('index.html: a <style> element');
  if (/\sstyle\s*=/i.test(html)) problems.push('index.html: a style attribute');
  for (const handler of html.matchAll(/\s(on[a-z]+)\s*=/gi)) {
    problems.push(`index.html: an event handler attribute, ${handler[1]}`);
  }
  for (const url of html.matchAll(/\s(?:src|href)\s*=\s*["']?([^"'\s>]+)/gi)) {
    const value = url[1];
    if (!value.startsWith('/') || value.startsWith('//')) {
      problems.push(`index.html: ${value} is not a root-relative URL`);
    }
  }
}

for (const file of files.filter((f) => extname(f) === '.css')) {
  const css = readFileSync(file, 'utf8');
  if (/@import\b/i.test(css)) problems.push(`${rel(file)}: @import`);
  for (const url of css.matchAll(/https?:\/\/[^\s)'"]+/gi)) {
    problems.push(`${rel(file)}: an absolute URL, ${url[0]}`);
  }
}

if (problems.length) {
  for (const problem of problems) console.error(problem);
  process.exit(1);
}
console.log(`${files.length} files in ${out} check out.`);
