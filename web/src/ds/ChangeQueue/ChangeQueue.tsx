import { Fragment, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react';
import { Button } from '../Button/Button';
import { ChangeGlyph } from '../ChangeBadge/ChangeBadge';
import type {
  Change,
  ChangeQueueProps,
  Outcome,
  OutcomeCounts,
  QueueGroup,
  QueueResult,
} from '../contract';
import { Icon } from '../Icon/Icon';
import { CHANGE_ORDER, CHANGES, changeNote } from '../lib/changes';
import { cx } from '../lib/cx';
import { fmtCount, fmtSeconds, plural } from '../lib/format';
import { useBand, useUid } from '../lib/hooks';
import { countOutcomes, isFailing, isOutcome, PYTEST_ORDER } from '../lib/outcomes';
import { visibleText } from '../lib/visible';
import { NodeId } from '../NodeId/NodeId';
import { OutcomeMark } from '../OutcomeMark/OutcomeMark';
import { rerunCommand } from '../RerunButton/RerunButton';
import { SegmentedControl } from '../SegmentedControl/SegmentedControl';

// Split from 900px of its own width, a window about 950px wide; narrower, one pane at a time.
const QUEUE_BANDS = [
  [900, 'split'],
  [0, 'single'],
] as const;

function isEditable(el: EventTarget | null): boolean {
  if (!el || !(el as Element).tagName) return false;
  const t = (el as Element).tagName;
  return (
    t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT' || !!(el as HTMLElement).isContentEditable
  );
}

function Kbd(t: string) {
  return (
    <kbd key={t} className="dl-kbd">
      {t}
    </kbd>
  );
}

// A row as the queue holds it, keyed by its node id; a test this run lacks has no outcome.
type Row = QueueResult & { key: string };

type GroupKey = QueueGroup | 'all';

interface GroupSpec {
  key: GroupKey;
  title?: string;
  change?: Change;
  items: Row[];
  total: number;
  step: number;
  collapsible?: boolean;
}

interface Group extends GroupSpec {
  whole: boolean;
  matched: Row[];
  collapsed: boolean;
  shown: Row[];
  hidden: number;
  unloaded: number;
}

// A run's tests in the order a person works through them: new failures first, then what still fails,
// what was fixed and what is new, each group in collection order. With renderDetail it is the whole
// triage view: the queue beside the selected test, or, narrower, one then the other.
export function ChangeQueue(p: ChangeQueueProps) {
  const results = p.results || [];
  const missing = p.missing || [];
  const base = p.baseline || null;
  const baseLabel = base ? base.label : null;
  // Paged by the server: totals say how many each group holds, the rows are what has loaded so far.
  const paged = !!p.totals;
  const totals = p.totals || {};
  const loading = new Set<string>(p.loading || []);
  const missKind: 'removed' | 'not-reached' =
    p.interrupted || (paged && totals['not-reached'] && !totals.removed)
      ? 'not-reached'
      : 'removed';
  const pageSize = p.pageSize || 50;
  const allStep = p.allPageSize || 200;
  const uid = useUid('dl-q');
  const [sizedBand, attach] = useBand(QUEUE_BANDS);
  const band = p.renderDetail ? sizedBand : 'list';
  const [view, setView] = useState<'changes' | 'all'>(p.defaultView || 'changes');
  const [q, setQ] = useState('');
  const [outFilter, setOutFilter] = useState<Outcome | 'all'>('all');
  const [more, setMore] = useState<Record<string, number>>({});
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [pane, setPane] = useState<'queue' | 'detail'>('queue');
  const [selOwn, setSelOwn] = useState<string | null>(null);
  const [copied, setCopied] = useState<{ key: string; ok: boolean; cmd: string } | null>(null);
  // A render to move focus in, even when the row it goes to is the one already selected.
  const [, setFocusTick] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);
  const filterRef = useRef<HTMLInputElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const focusSel = useRef(false);
  const focusDetail = useRef(false);
  const keyRef = useRef<((e: globalThis.KeyboardEvent) => void) | null>(null);
  const lastSel = useRef<string | null | undefined>(undefined);
  // The last selection brought into view, and how many times showing it opened or grew a group.
  const [revealed, setRevealed] = useState<{ key: string | null; n: number }>({ key: null, n: 0 });
  const lastReveal = useRef(0);

  // A missing test (removed or not reached) has no outcome here; paged, it comes with the other rows.
  function isGone(r: { change?: Change | null }) {
    return r.change === 'removed' || r.change === 'not-reached';
  }
  const keyed: Row[] = results.map((r) => ({
    key: r.nodeid,
    ...r,
    ...(isGone(r) ? { outcome: null } : null),
  }));
  const all = keyed.filter((r) => !isGone(r));
  const gone: Row[] = missing
    .map((r): Row => ({ key: r.nodeid, change: missKind, outcome: null, ...r }))
    .concat(keyed.filter(isGone));
  // The whole run in collection order: paged, what has loaded of it so far, already filtered by outcome.
  const runRows: Row[] =
    paged && p.allResults ? p.allResults.map((r) => ({ key: r.nodeid, ...r })) : all;
  const outNow = paged && p.outcome != null ? p.outcome : outFilter;
  const needle = q.trim().toLowerCase();
  function match(r: Row) {
    return !needle || String(r.nodeid).toLowerCase().indexOf(needle) >= 0;
  }
  function countOf(total: number | undefined, items: Row[]) {
    return paged && total != null ? total : items.length;
  }

  // The groups this view shows, before the filter and paging.
  const specs: GroupSpec[] = [];
  const notes: ReactNode[] = [];
  if (view === 'all') {
    const runItems = paged
      ? runRows
      : runRows.filter((r) => outNow === 'all' || r.outcome === outNow);
    const runTotal = paged
      ? outNow === 'all'
        ? p.total != null
          ? p.total
          : runItems.length
        : p.outcomeCounts?.[outNow] || 0
      : runItems.length;
    specs.push({ key: 'all', items: runItems, total: runTotal, step: allStep });
  } else if (base) {
    for (const c of CHANGE_ORDER) {
      const items = all.filter((r) => r.change === c);
      const total = countOf(totals[c], items);
      if (total)
        specs.push({
          key: c,
          title: CHANGES[c].group,
          change: c,
          items,
          total,
          step: paged ? allStep : Infinity,
        });
    }
    const goneTotal = countOf(totals[missKind], gone);
    if (goneTotal)
      specs.push({
        key: missKind,
        title: CHANGES[missKind].group,
        change: missKind,
        items: gone,
        total: goneTotal,
        step: paged ? allStep : Infinity,
        collapsible: missKind === 'removed',
      });
    const changedAny = specs.some((g) => g.key !== 'still-failing');
    if (!changedAny) {
      notes.push(
        <span key="same">
          Nothing changed since <code>{baseLabel}</code>.
        </span>,
      );
      if (!specs.length) notes.push(<span key="none">No failures.</span>);
    }
  } else {
    notes.push(<span key="nobase">{p.baselineNote || 'Nothing to compare with yet.'}</span>);
    const failing = all.filter((r) => isFailing(r.outcome));
    const failTotal = countOf(totals.failures, failing);
    if (failTotal)
      specs.push({
        key: 'failures',
        title: 'Failures',
        items: failing,
        total: failTotal,
        step: paged ? allStep : Infinity,
      });
    else notes.push(<span key="none">No failures.</span>);
  }

  // What is on screen, in order: the sequence j and k walk.
  const seq: Row[] = [];
  let unsearched = 0;
  const groups: Group[] = specs.map((g) => {
    const whole = g.items.length >= g.total;
    const matched = g.items.filter(match);
    const limit = more[g.key] ?? (g.key === 'all' ? allStep : pageSize);
    const collapsed = !!g.collapsible && !opened[g.key] && !needle;
    const shown = collapsed ? [] : matched.slice(0, limit);
    // Filtered, only loaded rows are searched; the rest of the group is counted, never implied.
    const hidden = collapsed ? 0 : needle ? matched.length - shown.length : g.total - shown.length;
    const unloaded = g.total - g.items.length;
    if (needle && unloaded > 0) unsearched += unloaded;
    for (const r of shown) seq.push(r);
    return { ...g, whole, matched, collapsed, shown, hidden, unloaded };
  });
  const byKey = new Map<string, Row>();
  for (const r of all.concat(gone, runRows)) byKey.set(r.key, r);
  // Asks for the next rows when what is wanted goes past what has loaded, or a group with none is opened.
  function fetchFor(g: Group, want: number) {
    if (
      paged &&
      p.onMore &&
      g.items.length < g.total &&
      want > g.items.length &&
      !loading.has(g.key)
    )
      p.onMore(g.key);
  }
  const selKey = p.selected != null ? p.selected : selOwn != null ? selOwn : seq[0]?.key;
  const cur = selKey != null ? (byKey.get(selKey) ?? null) : null;
  // A selection loaded past the rows its group shows, or in a closed group, as when an address
  // names it, is brought into view once: the group opens and shows rows, a step at a time, down to
  // it, as Show more would.
  if (selKey != null && selKey !== revealed.key) {
    const g = groups.find((x) => x.matched.some((r) => r.key === selKey));
    if (g) {
      const i = g.matched.findIndex((r) => r.key === selKey);
      const limit = more[g.key] ?? (g.key === 'all' ? allStep : pageSize);
      const grow = i >= limit;
      if (g.collapsed) setOpened({ ...opened, [g.key]: true });
      if (grow) {
        setMore({
          ...more,
          [g.key]:
            g.step === Infinity ? g.total : limit + Math.ceil((i + 1 - limit) / g.step) * g.step,
        });
      }
      setRevealed({ key: selKey, n: revealed.n + (g.collapsed || grow ? 1 : 0) });
    }
  }
  let at = -1;
  seq.forEach((r, i) => {
    if (r.key === selKey) at = i;
  });

  function commandFor(r: Row) {
    return p.commandFor ? p.commandFor(r) : rerunCommand([r.nodeid]);
  }
  function select(r: Row | null | undefined, how?: 'focus') {
    if (!r) return;
    if (p.selected == null) setSelOwn(r.key);
    if (p.onSelect) p.onSelect(r);
    if (how === 'focus') {
      focusSel.current = true;
      setFocusTick((n) => n + 1);
    }
  }
  function step(d: number, how?: 'focus') {
    if (!seq.length) return;
    const i = at < 0 ? (d > 0 ? 0 : seq.length - 1) : Math.max(0, Math.min(seq.length - 1, at + d));
    select(seq[i], how);
  }
  function showDetail() {
    setPane('detail');
    focusDetail.current = true;
  }
  function back() {
    setPane('queue');
    focusSel.current = true;
  }
  function open(r: Row | null) {
    if (band === 'single') showDetail();
    else if (p.onOpen && r) p.onOpen(r);
  }
  function copy(r: Row | null) {
    if (!r?.outcome) return;
    const cmd = commandFor(r);
    const key = r.key;
    function done(ok: boolean) {
      setCopied({ key, ok, cmd });
    }
    try {
      navigator.clipboard.writeText(cmd).then(
        () => done(true),
        () => done(false),
      );
    } catch {
      done(false);
    }
  }

  useEffect(() => {
    if (focusSel.current) {
      focusSel.current = false;
      const el = listRef.current?.querySelector<HTMLElement>(
        '[role="option"][aria-selected="true"]',
      );
      if (el) el.focus();
    }
    if (focusDetail.current && detailRef.current) {
      focusDetail.current = false;
      detailRef.current.focus();
    }
    // The selected row stays in view as the selection moves, whoever moved it, and once a group
    // opens or grows to show it; the first render leaves the page where it is.
    if (lastSel.current !== selKey || lastReveal.current !== revealed.n) {
      const first = lastSel.current === undefined && lastReveal.current === revealed.n;
      lastSel.current = selKey;
      lastReveal.current = revealed.n;
      const sel = first
        ? null
        : listRef.current?.querySelector<HTMLElement>('[role="option"][aria-selected="true"]');
      if (sel?.scrollIntoView) sel.scrollIntoView({ block: 'nearest' });
    }
  });
  useEffect(() => {
    if (!copied) return undefined;
    const t = setTimeout(() => setCopied(null), 4000);
    return () => clearTimeout(t);
  }, [copied]);

  function onListKey(e: KeyboardEvent<HTMLElement>) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const k = e.key;
    if (k === 'ArrowDown' || k === 'j') {
      e.preventDefault();
      step(1, 'focus');
    } else if (k === 'ArrowUp' || k === 'k') {
      e.preventDefault();
      step(-1, 'focus');
    } else if (k === 'Home') {
      e.preventDefault();
      select(seq[0], 'focus');
    } else if (k === 'End') {
      e.preventDefault();
      select(seq[seq.length - 1], 'focus');
    } else if (k === 'Enter') {
      e.preventDefault();
      open(cur);
    } else if (k === 'c') {
      e.preventDefault();
      copy(cur);
    }
  }
  // Page-wide keys, while no text field, menu or dialog has focus.
  keyRef.current = (e) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || isEditable(e.target)) return;
    const target = e.target as Element | null;
    if (target?.closest?.('dialog,[role="menu"]')) return;
    const k = e.key;
    if (k === 'j') {
      e.preventDefault();
      step(1);
    } else if (k === 'k') {
      e.preventDefault();
      step(-1);
    } else if (k === 'c') {
      e.preventDefault();
      copy(cur);
    } else if (k === '/') {
      e.preventDefault();
      if (band === 'single' && pane === 'detail') setPane('queue');
      setTimeout(() => {
        if (filterRef.current) filterRef.current.focus();
      }, 0);
    }
  };
  // Beside the detail, the queue fits the viewport below where it starts, so its foot is always in view;
  // as the page scrolls it grows until it sticks at the top.
  useEffect(() => {
    const el = boxRef.current;
    if (!el || band !== 'split') {
      if (el) el.style.maxHeight = '';
      return undefined;
    }
    let raf = 0;
    function fitHeight() {
      if (!el) return;
      raf = 0;
      const top = Math.max(el.getBoundingClientRect().top, 16);
      el.style.maxHeight = `${Math.max(240, window.innerHeight - top - 16)}px`;
    }
    function later() {
      if (!raf) raf = requestAnimationFrame(fitHeight);
    }
    fitHeight();
    window.addEventListener('scroll', later, { passive: true });
    window.addEventListener('resize', later);
    return () => {
      window.removeEventListener('scroll', later);
      window.removeEventListener('resize', later);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [band]);
  useEffect(() => {
    if (!p.shortcuts) return undefined;
    function onKey(e: globalThis.KeyboardEvent) {
      if (keyRef.current) keyRef.current(e);
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [p.shortcuts]);

  function option(r: Row) {
    const on = r.key === selKey;
    const note = view === 'all' ? null : changeNote(r, baseLabel);
    return (
      // biome-ignore lint/a11y/useKeyWithClickEvents: the listbox around the options takes the keys.
      <div
        key={r.key}
        role="option"
        id={`${uid}-${seq.indexOf(r)}`}
        aria-selected={on ? 'true' : 'false'}
        tabIndex={on || (at < 0 && r === seq[0]) ? 0 : -1}
        className="dl-qopt"
        onClick={() => {
          select(r);
          if (band === 'single') showDetail();
        }}
      >
        <span className="dl-qopt__mark">
          {r.outcome ? (
            <OutcomeMark outcome={r.outcome} size={14} />
          ) : (
            <ChangeGlyph change={r.change as Change} />
          )}
        </span>
        <span className="dl-qopt__id">
          <NodeId value={r.nodeid} truncate />
        </span>
        <span className="dl-qopt__dur">{r.seconds != null ? fmtSeconds(r.seconds) : ''}</span>
        {note ? <span className="dl-qopt__note">{note}</span> : null}
      </div>
    );
  }

  function groupHead(g: Group, gid: string) {
    // Filtered, a group not wholly loaded says how much of it was searched.
    const count = !needle
      ? fmtCount(g.total)
      : g.whole
        ? `${fmtCount(g.matched.length)} of ${fmtCount(g.total)}`
        : g.items.length
          ? `${fmtCount(g.matched.length)} of ${fmtCount(g.items.length)} loaded · ${fmtCount(g.total)} in all`
          : `none loaded · ${fmtCount(g.total)} in all`;
    const inner = [
      g.change ? <ChangeGlyph key="g" change={g.change} /> : null,
      <span key="t">{g.title}</span>,
      <span key="c" className="dl-queue__gcount">
        {count}
      </span>,
    ];
    if (!g.collapsible || needle)
      return (
        <h3 id={gid} className="dl-queue__ghead">
          {inner}
        </h3>
      );
    return (
      <h3 className="dl-queue__ghead">
        <button
          type="button"
          id={gid}
          className="dl-queue__gtoggle"
          aria-expanded={g.collapsed ? 'false' : 'true'}
          onClick={() => {
            const n = { ...opened, [g.key]: !opened[g.key] };
            setOpened(n);
            // A group with nothing loaded asks for its first rows as it opens.
            if (n[g.key]) fetchFor(g, pageSize);
          }}
        >
          <Icon name={g.collapsed ? 'chevron-right' : 'chevron-down'} size={14} />
          {inner}
        </button>
      </h3>
    );
  }

  function group(g: Group) {
    const gid = `${uid}-g-${g.key}`;
    const busy = loading.has(g.key);
    // Filtered, a group with no loaded match still shows while some of it is not loaded, so the rest can be searched.
    if (needle && !g.matched.length && (g.whole || g.collapsible)) return null;
    const showMore = () => {
      const want = g.step === Infinity ? g.total : g.shown.length + g.step;
      setMore({ ...more, [g.key]: want });
      fetchFor(g, needle ? g.items.length + 1 : want);
    };
    const left = needle ? g.hidden : Math.min(g.step, g.hidden);
    let more1: ReactNode = null;
    if (!g.collapsed && needle && g.unloaded > 0 && !g.hidden) {
      more1 = (
        <div className="dl-queue__more">
          <Button variant="quiet" size="sm" onClick={showMore} busy={busy} busyLabel="Loading">
            {`Load ${fmtCount(Math.min(allStep, g.unloaded))}${g.items.length ? ' more' : ''}`}
          </Button>
          <span>{`${fmtCount(g.unloaded)} not loaded, so not searched`}</span>
        </div>
      );
    } else if (!g.collapsed && g.hidden) {
      more1 = (
        <div className="dl-queue__more">
          <Button variant="quiet" size="sm" onClick={showMore} busy={busy} busyLabel="Loading">
            {g.shown.length ? `Show ${fmtCount(left)} more` : `Show ${fmtCount(left)}`}
          </Button>
          {g.step === Infinity || !g.shown.length ? null : (
            <span>{`${plural(g.hidden, 'result', 'results')} not shown`}</span>
          )}
        </div>
      );
    } else if (!g.collapsed && busy && !g.shown.length) {
      more1 = (
        <p className="dl-queue__more" role="status">
          Loading
          <span className="dl-btn__cursor" aria-hidden="true" />
        </p>
      );
    }
    return (
      <div key={g.key} className="dl-queue__group" aria-busy={busy ? 'true' : undefined}>
        {g.title ? groupHead(g, gid) : null}
        {g.shown.length ? (
          <div
            role="listbox"
            className="dl-queue__opts"
            aria-labelledby={g.title ? gid : undefined}
            aria-label={g.title ? undefined : 'Results'}
            onKeyDown={onListKey}
            aria-keyshortcuts="j k c Enter"
          >
            {g.shown.map(option)}
          </div>
        ) : null}
        {more1}
      </div>
    );
  }

  const anyMatch = groups.some((g) => g.matched.length);
  const total = p.total != null ? p.total : results.length;
  // Paged, the server counts and filters the whole run; whole, the rows are counted here.
  const outcomesHere: OutcomeCounts =
    paged && p.outcomeCounts
      ? p.outcomeCounts
      : countOutcomes(all.map((r) => r.outcome).filter(isOutcome));
  const outOpts = [{ value: 'all', label: `All ${fmtCount(paged ? total : all.length)}` }].concat(
    PYTEST_ORDER.filter((o) => isOutcome(o) && outcomesHere[o]).map((o) => ({
      value: o,
      label: `${o} ${fmtCount(outcomesHere[o] as number)}`,
    })),
  );
  function setOutcome(v: string) {
    const o = v as Outcome | 'all';
    if (!paged || p.outcome == null) setOutFilter(o);
    const n = { ...more };
    delete n.all;
    setMore(n);
    if (p.onOutcome) p.onOutcome(o);
  }
  function openAll() {
    setView('all');
    // The whole-run view asks for its first rows when none have loaded.
    if (paged && p.onMore && !runRows.length && total > 0 && !loading.has('all')) p.onMore('all');
  }
  const title =
    view === 'all' ? (
      plural(total, 'result', 'results')
    ) : base ? (
      <>
        Changed since <code>{baseLabel}</code>
      </>
    ) : (
      'This run'
    );
  const nNew = paged
    ? totals['new-failure'] || 0
    : all.filter((r) => r.change === 'new-failure').length;
  const listName = view === 'all' ? 'All results' : base ? 'Changed tests' : 'Failures';

  function keyPair(keys: string[], label: string) {
    const kids: ReactNode[] = [];
    keys.forEach((k, i) => {
      if (i) kids.push(' ');
      kids.push(Kbd(k));
    });
    return (
      <span key={label} className="dl-queue__key">
        {kids}
        {` ${label}`}
      </span>
    );
  }
  const list = (
    <div
      ref={boxRef}
      className="dl-queue__list"
      hidden={band === 'single' && pane === 'detail' ? true : undefined}
    >
      <div className="dl-queue__bar">
        {view === 'all' ? (
          <button type="button" className="dl-queue__switch" onClick={() => setView('changes')}>
            <Icon name="chevron-left" size={14} />
            {base ? 'Changed tests' : 'Failures'}
          </button>
        ) : null}
        <h2 className="dl-queue__title">{title}</h2>
        <label className="dl-filters__search dl-queue__search">
          <span className="dl-sr">
            {view === 'all' ? 'Filter results by node id' : 'Filter changed tests by node id'}
          </span>
          <Icon name="search" size={16} />
          <input
            ref={filterRef}
            className="dl-input dl-input--mono"
            type="search"
            dir="ltr"
            placeholder="Filter by node id"
            value={q}
            aria-keyshortcuts={p.shortcuts ? '/' : undefined}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && q) {
                e.preventDefault();
                setQ('');
              } else if (e.key === 'ArrowDown' && seq.length) {
                e.preventDefault();
                select(at < 0 ? seq[0] : seq[at], 'focus');
              }
            }}
          />
        </label>
        {/* Every outcome and its count stays in view: the choices wrap rather than scroll out of sight. */}
        {view === 'all' && outOpts.length > 2 ? (
          <SegmentedControl
            label="Outcome"
            options={outOpts}
            value={outNow}
            onChange={setOutcome}
            className="dl-seg--wrap"
          />
        ) : null}
      </div>
      <div className="dl-queue__scroll" ref={listRef}>
        {notes.length && !needle ? <p className="dl-queue__note">{notes}</p> : null}
        {groups.map(group)}
        {needle && !anyMatch ? (
          <p className="dl-queue__note">
            {`No ${unsearched ? 'loaded ' : ''}${view === 'all' ? 'result' : 'test here'} matches `}
            <code>{q.trim()}</code>
            {unsearched ? `; ${fmtCount(unsearched)} not loaded were not searched.` : '.'}
          </p>
        ) : null}
      </div>
      {view === 'all' ? null : (
        <button type="button" className="dl-queue__all" onClick={openAll}>
          {`All ${plural(total, 'result', 'results')}`}
          <Icon name="chevron-right" size={14} />
        </button>
      )}
      <div className="dl-queue__foot" role="status">
        {copied ? (
          copied.ok ? (
            <>
              <Icon name="check" size={14} />
              Copied <code>{copied.cmd}</code>
            </>
          ) : (
            <>
              The clipboard refused; copy it from here:{' '}
              <code className="dl-queue__cmd">{copied.cmd}</code>
            </>
          )
        ) : (
          [
            keyPair(['j', 'k'], 'move'),
            keyPair(['Enter'], band === 'single' ? 'shows the test' : 'opens the result'),
            keyPair(['c'], 'copies its rerun command'),
            p.shortcuts ? keyPair(['/'], 'filters') : null,
          ]
        )}
      </div>
      {/* A running run is compared once it ends, so until then its queue counts failures, not changes. */}
      <span className="dl-sr" aria-live="polite">
        {!p.running
          ? ''
          : base
            ? `${plural(nNew, 'new failure', 'new failures')} so far`
            : `${plural(
                paged ? totals.failures || 0 : all.filter((r) => isFailing(r.outcome)).length,
                'failure',
                'failures',
              )} so far`}
      </span>
    </div>
  );

  if (!p.renderDetail)
    return (
      <section
        ref={attach}
        className={cx('dl-queue', p.className)}
        data-band={band}
        aria-label={p.label || listName}
      >
        {list}
      </section>
    );

  const pos = at >= 0 ? ` (${fmtCount(at + 1)} of ${fmtCount(seq.length)})` : '';
  const detail = (
    <div
      ref={detailRef}
      className="dl-queue__detail"
      tabIndex={-1}
      role="region"
      aria-label={cur ? `Selected test: ${visibleText(cur.nodeid)}` : 'Selected test'}
      hidden={band === 'single' && pane === 'queue' ? true : undefined}
    >
      {band === 'single' ? (
        <div className="dl-queue__back">
          <button type="button" className="dl-queue__switch" onClick={back}>
            <Icon name="chevron-left" size={14} />
            {listName + pos}
          </button>
          <span className="dl-actions">
            <Button
              variant="quiet"
              size="sm"
              icon="chevron-up"
              label="Previous test"
              onClick={() => step(-1)}
            />
            <Button
              variant="quiet"
              size="sm"
              icon="chevron-down"
              label="Next test"
              onClick={() => step(1)}
            />
          </span>
        </div>
      ) : null}
      {cur ? (
        <Fragment key={cur.key}>
          {p.renderDetail(cur, {
            command: cur.outcome ? commandFor(cur) : null,
            baseline: baseLabel,
            index: at,
            count: seq.length,
          })}
        </Fragment>
      ) : (
        <p className="dl-queue__note">Choose a test to see its evidence.</p>
      )}
    </div>
  );
  return (
    <section
      ref={attach}
      className={cx('dl-queue', `dl-queue--${band}`, p.className)}
      data-band={band}
      aria-label={p.label || 'Triage'}
    >
      {list}
      {detail}
    </section>
  );
}
