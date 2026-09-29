import type { IconName, IconProps } from '../contract';
import { cx } from '../lib/cx';

// 16-unit grid, 1.5 stroke, round caps and joins. "c:" is a circle, "r:" a rect, anything else a path.
export const ICONS: Record<IconName, string[]> = {
  check: ['M3.5 8.5l3 3 6-7'],
  cross: ['M4.5 4.5l7 7M11.5 4.5l-7 7'],
  dash: ['M4 8h8'],
  alert: ['M8 3v6', 'M8 12.25v.5'],
  'circle-cross': ['c:8,8,5.75', 'M6.25 6.25l3.5 3.5M9.75 6.25l-3.5 3.5'],
  'circle-check': ['c:8,8,5.75', 'M5.5 8.25l1.75 1.75 3.25-3.75'],
  pin: ['M6.5 2.5h3V6l2 3h-7l2-3z', 'M8 9v4.5'],
  lock: ['r:3,7,10,6.5,1.5', 'M5.5 7V5a2.5 2.5 0 0 1 5 0v2'],
  users: [
    'c:6,5.5,2',
    'M2.5 13c0-2 1.6-3.5 3.5-3.5S9.5 11 9.5 13',
    'c:11,6,1.5',
    'M11 9.5c1.5 0 2.5 1.1 2.5 2.75',
  ],
  user: ['c:8,5.5,2.5', 'M3 13.5c0-2.75 2.25-4.5 5-4.5s5 1.75 5 4.5'],
  link: [
    'M7 9a2.5 2.5 0 0 0 3.5 0l2-2A2.5 2.5 0 0 0 9 3.5l-.75.75',
    'M9 7a2.5 2.5 0 0 0-3.5 0l-2 2A2.5 2.5 0 0 0 7 12.5l.75-.75',
  ],
  eye: ['M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z', 'c:8,8,1.75'],
  pencil: ['M10.5 3L13 5.5 6 12.5H3.5V10z'],
  key: ['c:5.5,10.5,2.5', 'M7.25 8.75L13 3', 'M11 5l1.5 1.5'],
  'chevron-down': ['M4.5 6.5L8 10l3.5-3.5'],
  'chevron-right': ['M6.5 4.5L10 8l-3.5 3.5'],
  'chevron-left': ['M9.5 4.5L6 8l3.5 3.5'],
  'chevron-up': ['M4.5 9.5L8 6l3.5 3.5'],
  'circle-minus': ['c:8,8,5.75', 'M5.5 8h5'],
  search: ['c:7,7,4.25', 'M10.25 10.25l3.25 3.25'],
  filter: ['M2.5 3.5h11l-4.25 5v4l-2.5 1.5V8.5z'],
  copy: ['r:5.5,5.5,8,8,1.5', 'M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2'],
  download: ['M8 2.5v8', 'M4.5 7.5L8 11l3.5-3.5', 'M3 13.5h10'],
  clock: ['c:8,8,5.75', 'M8 5v3.25l2 1.25'],
  branch: [
    'c:5,3.75,1.5',
    'c:5,12.25,1.5',
    'c:11,5.25,1.5',
    'M5 5.25v5.5',
    'M11 6.75c0 2.75-6 1.75-6 4',
  ],
  commit: ['c:8,8,2.5', 'M1.5 8h4M10.5 8h4'],
  square: ['r:4.5,4.5,7,7,1'],
  trash: ['M3 4.5h10', 'M6.5 4.5V3h3v1.5', 'M4.5 4.5l.75 9h5.5l.75-9'],
  plus: ['M8 3v10M3 8h10'],
  external: [
    'M9 2.5h4.5V7',
    'M13.5 2.5l-6 6',
    'M11.5 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3',
  ],
  sliders: ['M2.5 4.5h6M12 4.5h1.5M2.5 11.5h1.5M7 11.5h6.5', 'c:10.25,4.5,1.75', 'c:5.5,11.5,1.75'],
  'log-out': ['M6 2.5H3.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1H6', 'M10 5l3 3-3 3', 'M13 8H6'],
  archive: ['r:2,3,12,3,1', 'M3 6v6.5a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V6', 'M6.5 9h3'],
  plug: ['M6 2v3.5M10 2v3.5', 'M4 5.5h8V8a4 4 0 0 1-8 0z', 'M8 12v2.5'],
  dice: ['r:2.5,2.5,11,11,2', 'M5.5 5.5v.01M10.5 10.5v.01M8 8v.01'],
  target: ['c:8,8,5.75', 'c:8,8,2.75', 'M8 8v.01'],
  flask: ['M6 2.5h4', 'M6.75 2.5v4L3.2 12.4a1 1 0 0 0 .87 1.6h7.86a1 1 0 0 0 .87-1.6L9.25 6.5v-4'],
  terminal: ['r:1.5,2.5,13,11,1.5', 'M4.5 6l2 2-2 2', 'M8.5 10.5h3'],
  info: ['c:8,8,5.75', 'M8 7.25V11', 'M8 4.9v.2'],
  warning: ['M8 2.5l6 11H2z', 'M8 6.5v3', 'M8 11.5v.25'],
  more: ['M3.5 8v.01M8 8v.01M12.5 8v.01'],
  menu: ['M2.5 4.5h11M2.5 8h11M2.5 11.5h11'],
  play: ['M5 3.5v9l7-4.5z'],
};

// Glyphs that point along the reading direction; they mirror in a right-to-left page.
const DIRECTIONAL: Partial<Record<IconName, true>> = {
  'chevron-right': true,
  'chevron-left': true,
  'log-out': true,
  external: true,
};

export function Icon(p: IconProps) {
  const size = p.size || 16;
  const shapes = (ICONS[p.name] || ICONS.dash).map((s, i) => {
    // A glyph's shapes are a fixed list that never reorders, so its index is its identity.
    if (s.startsWith('c:')) {
      const [cx0, cy, r] = s.slice(2).split(',');
      // biome-ignore lint/suspicious/noArrayIndexKey: a glyph's shapes are a fixed list.
      return <circle key={i} cx={cx0} cy={cy} r={r} />;
    }
    if (s.startsWith('r:')) {
      const [x, y, w, h, rx] = s.slice(2).split(',');
      // biome-ignore lint/suspicious/noArrayIndexKey: a glyph's shapes are a fixed list.
      return <rect key={i} x={x} y={y} width={w} height={h} rx={rx || 0} />;
    }
    // biome-ignore lint/suspicious/noArrayIndexKey: a glyph's shapes are a fixed list.
    return <path key={i} d={s} />;
  });
  return (
    <svg
      className={cx('dl-icon', DIRECTIONAL[p.name] && 'dl-icon--dir', p.className)}
      viewBox="0 0 16 16"
      width={size}
      height={size}
      focusable="false"
      role={p.title ? 'img' : undefined}
      aria-hidden={p.title ? undefined : 'true'}
    >
      {p.title ? <title>{p.title}</title> : null}
      {shapes}
    </svg>
  );
}
