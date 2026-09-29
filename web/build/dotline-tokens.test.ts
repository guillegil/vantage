import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { type Tokens, tokensCss } from './dotline-tokens.ts';

const tokens = JSON.parse(
  readFileSync(resolve(import.meta.dirname, '../src/ds/tokens.json'), 'utf8'),
) as Tokens;
const css = tokensCss(tokens);

function blockAfter(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf('}', start));
}

describe('tokensCss', () => {
  it('turns an alias into a reference to its target', () => {
    expect(blockAfter(':root')).toContain('--focus: var(--accent);');
    expect(blockAfter(':root')).toContain('--danger: var(--failed);');
  });

  it('declares the dark values, aliases included, under both dark selectors', () => {
    expect(css).toContain(
      '@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {',
    );
    for (const selector of [':root:not([data-theme="light"])', ':root[data-theme="dark"]']) {
      const block = blockAfter(selector);
      expect(block).toContain('color-scheme: dark;');
      expect(block).toContain('--page: #0e1318;');
      expect(block).toContain('--focus: var(--accent);');
      expect(block).toContain('--abandoned: var(--ink-faint);');
      expect(block).not.toContain('--space-1');
    }
  });

  it('declares the light values, sizes and fonts once on :root', () => {
    const root = blockAfter(':root');
    expect(root).toContain('color-scheme: light;');
    expect(root).toContain('--page: #edf0f3;');
    expect(root).toContain('--space-4: 16px;');
    expect(root).toContain('--font-mono: "Chivo Mono"');
  });

  it('loads both faces from beside the tokens, swapping in', () => {
    expect(css).toContain('src: url("./fonts/Chivo-Variable.woff2") format("woff2");');
    expect(css).toContain('src: url("./fonts/ChivoMono-Variable.woff2") format("woff2");');
    expect(css.match(/font-display: swap;/g)).toHaveLength(2);
  });

  it('defines the type classes with their family', () => {
    expect(blockAfter('.t-display')).toContain('font-family: var(--font-sans);');
    expect(blockAfter('.t-display')).toContain('font-size: 28px;');
    expect(blockAfter('.t-code')).toContain('font-family: var(--font-mono);');
    expect(blockAfter('.t-label')).toContain('letter-spacing: 0.01em;');
  });
});
