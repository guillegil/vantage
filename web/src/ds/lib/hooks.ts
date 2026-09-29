import {
  type KeyboardEvent,
  type RefObject,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react';

export interface MenuOptions {
  open?: boolean;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
}

export interface Menu {
  open: boolean;
  set: (next: boolean, focusButton?: boolean) => void;
  toggle: () => void;
  root: RefObject<HTMLDivElement | null>;
  btn: RefObject<HTMLButtonElement | null>;
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
}

// A menu button's behaviour: Escape or a click outside closes it, arrows move between items,
// and opening it from the button moves focus to the first item.
export function useMenu(p: MenuOptions): Menu {
  const [own, setOwn] = useState(!!p.defaultOpen);
  const open = p.open != null ? p.open : own;
  const root = useRef<HTMLDivElement | null>(null);
  const btn = useRef<HTMLButtonElement | null>(null);
  const focusFirst = useRef(false);
  const onToggle = useRef(p.onToggle);
  onToggle.current = p.onToggle;
  const controlled = p.open != null;
  const set = useCallback(
    (next: boolean, focusButton?: boolean) => {
      if (!controlled) setOwn(next);
      if (onToggle.current) onToggle.current(next);
      if (!next && focusButton && btn.current) btn.current.focus();
    },
    [controlled],
  );
  useEffect(() => {
    if (!open) return undefined;
    if (focusFirst.current) {
      focusFirst.current = false;
      const first = root.current?.querySelector<HTMLElement>('[role="menuitem"]');
      if (first) first.focus();
    }
    function onDown(e: MouseEvent) {
      if (root.current && !root.current.contains(e.target as Node)) set(false);
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open, set]);
  function toggle() {
    focusFirst.current = !open;
    set(!open);
  }
  function onKeyDown(e: KeyboardEvent<HTMLElement>) {
    if (!open) {
      if (e.key === 'ArrowDown' && e.target === btn.current) {
        e.preventDefault();
        focusFirst.current = true;
        set(true);
      }
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      set(false, true);
      return;
    }
    if (e.key === 'Tab') {
      set(false);
      return;
    }
    const items = Array.from(
      root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );
    if (!items.length) return;
    const i = items.indexOf(document.activeElement as HTMLElement);
    let j: number | null = null;
    if (e.key === 'ArrowDown') j = i < 0 ? 0 : (i + 1) % items.length;
    else if (e.key === 'ArrowUp')
      j = i < 0 ? items.length - 1 : (i - 1 + items.length) % items.length;
    else if (e.key === 'Home') j = 0;
    else if (e.key === 'End') j = items.length - 1;
    if (j != null) {
      e.preventDefault();
      items[j]?.focus();
    }
  }
  return { open, set, toggle, root, btn, onKeyDown };
}

// The width band a component sits in, measured from its own box rather than the window,
// so it adapts the same in a full page, a split pane or a side column. Returns [band, ref].
export function useBand<B extends string>(
  bands: readonly (readonly [number, B])[],
): [B, (el: HTMLElement | null) => void] {
  const [band, setBand] = useState<B>((bands[0] as readonly [number, B])[1]);
  const ro = useRef<ResizeObserver | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: the bands are a module constant.
  const attach = useCallback((el: HTMLElement | null) => {
    if (ro.current) {
      ro.current.disconnect();
      ro.current = null;
    }
    if (!el || typeof ResizeObserver === 'undefined') return;
    function check() {
      if (!el) return;
      const w = el.getBoundingClientRect().width;
      for (const [min, name] of bands) {
        if (w >= min) {
          setBand(name);
          return;
        }
      }
    }
    check();
    ro.current = new ResizeObserver(check);
    ro.current.observe(el);
  }, []);
  return [band, attach];
}

// A component's own width in whole pixels, for drawings that fill their box. Returns [width, ref].
export function useWidth(): [number, (el: HTMLElement | null) => void] {
  const [width, setWidth] = useState(0);
  const ro = useRef<ResizeObserver | null>(null);
  const attach = useCallback((el: HTMLElement | null) => {
    if (ro.current) {
      ro.current.disconnect();
      ro.current = null;
    }
    if (!el || typeof ResizeObserver === 'undefined') return;
    function check() {
      if (!el) return;
      const w = Math.floor(el.getBoundingClientRect().width);
      setWidth((prev) => (Math.abs(prev - w) > 1 ? w : prev));
    }
    check();
    ro.current = new ResizeObserver(check);
    ro.current.observe(el);
  }, []);
  return [width, attach];
}

// Ids for label and description pairs: unique per instance, and safe in CSS selectors.
export function useUid(prefix?: string): string {
  return `${prefix || 'dl'}-${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
}

// A scroll container that actually overflows becomes a focusable, named region,
// so a keyboard can scroll it; one that fits adds no tab stop.
export function useOverflowFocus(ref: RefObject<HTMLElement | null>): boolean {
  const [over, setOver] = useState(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: measured once the element exists, as the design system does.
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    function check() {
      if (!el) return;
      setOver(el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1);
    }
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, []);
  return over;
}
