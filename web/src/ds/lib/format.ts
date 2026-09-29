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
