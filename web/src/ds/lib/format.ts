import type { ValueFormat } from '../contract';

export function pad2(n: number): string {
  return (n < 10 ? '0' : '') + n;
}

export function toDate(v: string | Date): Date {
  return v instanceof Date ? v : new Date(v);
}

function invalid(v: string | Date | null | undefined): v is null | undefined {
  return v == null || Number.isNaN(toDate(v).getTime());
}

// Figures in the interface are grouped the English way, to match the copy around them.
// pytest's own lines (SummaryLine, the chars dotline) keep pytest's ungrouped numbers.
const COUNT_FMT =
  typeof Intl !== 'undefined' && Intl.NumberFormat ? new Intl.NumberFormat('en') : null;

export function fmtCount(n: number): string {
  return COUNT_FMT ? COUNT_FMT.format(n) : String(n);
}

export function plural(n: number, one: string, many: string): string {
  return `${fmtCount(n)} ${n === 1 ? one : many}`;
}

// Durations people read: 184 ms, 4.21 s, 1 m 52 s, 2 h 5 m.
export function fmtSeconds(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return '—';
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 10) return `${s.toFixed(2)} s`;
  if (s < 60) return `${s.toFixed(1)} s`;
  const t = Math.round(s);
  if (t < 3600) return `${Math.floor(t / 60)} m ${t % 60} s`;
  return `${fmtCount(Math.floor(t / 3600))} h ${Math.floor((t % 3600) / 60)} m`;
}

// pytest's own session duration: "48.21s", or "125.31s (0:02:05)" past a minute.
export function fmtPytestSeconds(s: number): string {
  let out = `${s.toFixed(2)}s`;
  if (s >= 60) {
    const t = Math.floor(s);
    out += ` (${Math.floor(t / 3600)}:${pad2(Math.floor((t % 3600) / 60))}:${pad2(t % 60)})`;
  }
  return out;
}

export function fmtAbsolute(v: string | Date | null | undefined): string {
  if (invalid(v)) return '—';
  const d = toDate(v);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(
    d.getUTCHours(),
  )}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())} UTC`;
}

export function fmtRelative(v: string | Date | null | undefined, now?: string | Date): string {
  if (invalid(v)) return '—';
  const d = toDate(v);
  const s = Math.round(((now ? toDate(now) : new Date()).getTime() - d.getTime()) / 1000);
  // A browser clock a little ahead of the server's is not "the future".
  if (s < 0) return s > -120 ? 'just now' : fmtAbsolute(v);
  if (s < 60) return `${s} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const hr = Math.floor(m / 60);
  if (hr < 24) return `${hr} h ago`;
  const days = Math.floor(hr / 24);
  if (days < 30) return `${days} d ago`;
  return fmtAbsolute(v).slice(0, 10);
}

// How much earlier one run started than another, in the words a run list's times use.
export function fmtEarlier(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 1) return 'under 1 s';
  const t = Math.floor(s);
  if (t < 60) return `${t} s`;
  const m = Math.floor(t / 60);
  if (m < 60) return `${m} min`;
  const hr = Math.floor(m / 60);
  if (hr < 24) return `${hr} h`;
  return `${fmtCount(Math.floor(hr / 24))} d`;
}

// A word or phrase with its first letter in capitals: "new failure" heads a sentence as "New failure".
export function capital(t: string): string {
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// Bytes the IEC way, as exports print them: 512 B, 12.3 KiB, 184 MiB.
export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return `${fmtCount(Math.round(n))} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 ? v.toFixed(1).replace(/\.0$/, '') : fmtCount(Math.round(v))} ${units[i]}`;
}

const NUM_FMT = new Map<string, Intl.NumberFormat | null>();

export function fmtNumber(v: unknown, decimals?: number): string {
  // A string of digits counts, as the design system's isFinite counts it.
  if (v == null || !Number.isFinite(Number(v))) return '—';
  // A value keeps the precision it was recorded with, up to four decimals; ticks pass their own.
  const fixed = decimals != null;
  const d = fixed ? decimals : 4;
  const key = (fixed ? 'f' : 'v') + d;
  if (!NUM_FMT.has(key))
    NUM_FMT.set(
      key,
      typeof Intl !== 'undefined'
        ? new Intl.NumberFormat('en', {
            minimumFractionDigits: fixed ? d : 0,
            maximumFractionDigits: d,
          })
        : null,
    );
  const f = NUM_FMT.get(key);
  const n = Number(v);
  return f ? f.format(n) : fixed ? n.toFixed(d) : String(+n.toFixed(d));
}

// One value as a chart or a table prints it. Seconds and bytes carry their own units.
export function fmtValue(
  v: unknown,
  format?: ValueFormat,
  unit?: string,
  decimals?: number,
): string {
  if (v == null || v === '' || (typeof v === 'number' && !Number.isFinite(v))) return '—';
  if (typeof format === 'function') return format(v as number);
  let out: string;
  if (format === 'count') out = fmtCount(Math.round(Number(v)));
  else if (format === 'seconds') return fmtSeconds(Number(v));
  else if (format === 'bytes') return fmtBytes(Number(v));
  else if (format === 'percent')
    out = `${fmtNumber(v, decimals != null ? decimals : Math.round(Number(v)) === v ? 0 : 1)}%`;
  else if (format === 'text' || typeof v !== 'number') return String(v);
  else out = fmtNumber(v, decimals);
  return unit ? `${out} ${unit}` : out;
}
