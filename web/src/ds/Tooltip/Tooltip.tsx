import {
  Children,
  cloneElement,
  type FocusEvent,
  type ReactElement,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type { TooltipProps } from '../contract';
import { chain, cx } from '../lib/cx';
import { useUid } from '../lib/hooks';

interface TriggerProps {
  'aria-describedby'?: string;
  onFocus?: (e: FocusEvent) => void;
  onBlur?: (e: FocusEvent) => void;
}

// A short explanation for one control, shown on hover and on keyboard focus, and linked to the
// control by aria-describedby. It is fixed-positioned, so no scroll container or panel clips it.
export function Tooltip(p: TooltipProps) {
  const id = useUid('dl-tip');
  const [own, setOwn] = useState(false);
  const open = p.open != null ? p.open : own;
  const wrap = useRef<HTMLSpanElement | null>(null);
  const tip = useRef<HTMLSpanElement | null>(null);
  const child = Children.only(p.children) as ReactElement<TriggerProps>;
  function show() {
    setOwn(true);
  }
  function hide() {
    setOwn(false);
  }
  useLayoutEffect(() => {
    if (!open || !wrap.current || !tip.current) return undefined;
    const el = tip.current;
    // Place it at 0,0 first: where that lands is its containing block's origin, which a transformed
    // or contained ancestor can move away from the viewport's.
    el.style.left = '0px';
    el.style.top = '0px';
    const t = el.getBoundingClientRect();
    const r = wrap.current.getBoundingClientRect();
    const below = r.top < t.height + 12;
    const left = Math.max(
      8,
      Math.min(window.innerWidth - t.width - 8, r.left + r.width / 2 - t.width / 2),
    );
    el.style.left = `${left - t.left}px`;
    el.style.top = `${(below ? r.bottom + 6 : r.top - t.height - 6) - t.top}px`;
    el.style.visibility = 'visible';
    function onScroll() {
      setOwn(false);
    }
    window.addEventListener('scroll', onScroll, true);
    return () => window.removeEventListener('scroll', onScroll, true);
  }, [open]);
  const trigger = cloneElement(child, {
    'aria-describedby':
      p.describe === false
        ? child.props['aria-describedby']
        : cx(child.props['aria-describedby'], id),
    onFocus: chain(child.props.onFocus, show),
    onBlur: chain(child.props.onBlur, hide),
  });
  return (
    // Hover shows the explanation; the trigger inside is what takes focus and keys.
    <span
      ref={wrap}
      className={cx('dl-tip', p.className)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.stopPropagation();
          hide();
        }
      }}
    >
      {trigger}
      <span ref={tip} id={id} role="tooltip" className="dl-tip__bubble" hidden={!open}>
        {p.content}
      </span>
    </span>
  );
}
