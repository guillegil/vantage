import { describe, expect, it } from 'vitest';
import { inAppPath } from './links';

const ORIGIN = window.location.origin;

function click(html: string, init: MouseEventInit = {}): string | null {
  document.body.replaceChildren();
  const holder = document.createElement('div');
  holder.append(document.createRange().createContextualFragment(html));
  document.body.append(holder);
  const target = holder.querySelector('[data-click]') ?? holder.firstElementChild;
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ...init });
  Object.defineProperty(event, 'target', { value: target });
  return inAppPath(event, ORIGIN);
}

describe('inAppPath', () => {
  it('routes a plain click on a link within the client', () => {
    expect(click('<a href="/runs/abc?x=1"><span data-click>abc</span></a>')).toBe('/runs/abc?x=1');
  });

  it.each([
    ['a modifier', '<a href="/runs/abc">x</a>', { ctrlKey: true }],
    ['another button', '<a href="/runs/abc">x</a>', { button: 1 }],
    ['a new window', '<a href="/runs/abc" target="_blank">x</a>', {}],
    ['a download', '<a href="/runs/abc" download>x</a>', {}],
    ['the API', '<a href="/api/v1/openapi.yaml">x</a>', {}],
    ['a built file', '<a href="/fonts/OFL.txt">x</a>', {}],
    ['another origin', '<a href="https://example.com/">x</a>', {}],
    ['a fragment', '<a href="#main">x</a>', {}],
  ])('leaves %s to the browser', (_why, html, init) => {
    expect(click(html, init as MouseEventInit)).toBeNull();
  });
});
