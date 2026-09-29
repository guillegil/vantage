// The design system's tokens.json as CSS, made on the fly, so there is no
// generated stylesheet to drift from it. `virtual:dotline-tokens.css`
// resolves to a file name beside tokens.json that does not exist on disk:
// the CSS pipeline then resolves, hashes and emits the fonts' relative
// `url()`s as it does for any stylesheet there. Dev, build and Vitest run
// the same code.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import type { Plugin } from 'vite';

export const VIRTUAL_ID = 'virtual:dotline-tokens.css';
const FILE_NAME = 'dotline-tokens.virtual.css';

type Themed = Record<string, string>;
type TokenValue = string | Themed;

interface Token {
  name: string;
  value: TokenValue;
}

interface TypeStyle {
  name: string;
  fontSize?: string;
  lineHeight?: string;
  fontWeight?: number | string;
  letterSpacing?: string;
}

export interface Tokens {
  color: { themes: { id: string }[]; tokens: Token[] };
  type: {
    fonts: { family: string; file: string; weight: string; style: string }[];
    families: Record<string, string>;
    groups: { family: string; styles: TypeStyle[] }[];
  };
  [section: string]: unknown;
}

// A value `{name}` names another token: it becomes var(--name), so an alias
// follows its target in every theme.
function cssValue(value: string): string {
  return value.replace(/\{([a-z0-9-]+)\}/g, 'var(--$1)');
}

function isAlias(value: string): boolean {
  return /\{[a-z0-9-]+\}/.test(value);
}

function tokenSections(tokens: Tokens): Token[] {
  const out: Token[] = [...tokens.color.tokens];
  for (const [key, section] of Object.entries(tokens)) {
    if (key === 'color' || key === 'type') continue;
    const list = (section as { tokens?: unknown } | null)?.tokens;
    if (Array.isArray(list)) out.push(...(list as Token[]));
  }
  return out;
}

// The declarations one theme block holds. Every themed value is declared in
// each block, and so is every alias, since var() is resolved where the custom
// property is declared: an alias left in :root alone would keep light values
// inside a dark subtree.
function declarations(all: Token[], theme: string, first: boolean): string[] {
  const lines: string[] = [];
  for (const token of all) {
    const { value } = token;
    if (typeof value === 'string') {
      if (first || isAlias(value)) lines.push(`--${token.name}: ${cssValue(value)};`);
      continue;
    }
    const themed = value[theme];
    if (themed !== undefined) lines.push(`--${token.name}: ${cssValue(themed)};`);
  }
  return lines;
}

function block(selector: string, lines: string[], indent = ''): string {
  const body = lines.map((line) => `${indent}  ${line}`).join('\n');
  return `${indent}${selector} {\n${body}\n${indent}}`;
}

function kebab(name: string): string {
  return name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
}

export function tokensCss(tokens: Tokens): string {
  const parts: string[] = [];
  for (const font of tokens.type.fonts) {
    parts.push(
      block('@font-face', [
        `font-family: ${JSON.stringify(font.family)};`,
        `src: url(${JSON.stringify(`./${font.file}`)}) format("woff2");`,
        `font-weight: ${font.weight};`,
        `font-style: ${font.style};`,
        'font-display: swap;',
      ]),
    );
  }
  const all = tokenSections(tokens);
  const themes = tokens.color.themes.map((t) => t.id);
  const [light = 'light', ...others] = themes;
  const families = Object.entries(tokens.type.families).map(
    ([key, stack]) => `--font-${key}: ${stack};`,
  );
  parts.push(
    block(':root', [`color-scheme: ${light};`, ...families, ...declarations(all, light, true)]),
  );
  for (const theme of others) {
    const lines = [`color-scheme: ${theme};`, ...declarations(all, theme, false)];
    // Followed from the system unless a page forces the other theme.
    parts.push(
      `@media (prefers-color-scheme: ${theme}) {\n${block(`:root:not([data-theme="${light}"])`, lines, '  ')}\n}`,
    );
    parts.push(block(`:root[data-theme="${theme}"]`, lines));
  }
  for (const group of tokens.type.groups) {
    for (const style of group.styles) {
      const lines = [`font-family: var(--font-${group.family});`];
      for (const key of ['fontSize', 'lineHeight', 'fontWeight', 'letterSpacing'] as const) {
        const v = style[key];
        if (v !== undefined) lines.push(`${kebab(key)}: ${v};`);
      }
      parts.push(block(`.${style.name}`, lines));
    }
  }
  return `${parts.join('\n\n')}\n`;
}

export function dotlineTokens(tokensPath: string): Plugin {
  const source = resolve(tokensPath);
  const id = resolve(dirname(source), FILE_NAME);
  return {
    name: 'dotline-tokens',
    enforce: 'pre',
    resolveId(request) {
      return request === VIRTUAL_ID ? id : null;
    },
    load(request) {
      if (request !== id) return null;
      this.addWatchFile(source);
      return tokensCss(JSON.parse(readFileSync(source, 'utf8')) as Tokens);
    },
  };
}
