// Text a test printed, named or asserted reaches the page as text: every
// component that prints recorded text renders these as characters and
// creates no element from them.
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it } from 'vitest';
import {
  AppBar,
  Command,
  CommitRef,
  DataTable,
  EmptyState,
  Evidence,
  HistoryGrid,
  MetaList,
  NodeId,
  Notice,
  Panel,
  PhaseTimeline,
  RecordingGuide,
  RunList,
  UserChip,
} from './index';

const PAYLOADS = [
  '<img src=x onerror=window.__pwned=1>',
  '</script><script>window.__pwned=1</script>',
];

function cases(s: string): [string, ReactElement][] {
  return [
    ['NodeId', <NodeId key="n" value={`tests/test_a.py::test_x[${s}]`} />],
    ['CommitRef', <CommitRef key="c" branch={s} sha="abcdef0123" subject={s} />],
    ['MetaList', <MetaList key="m" items={[{ key: s, value: s, source: s }]} />],
    ['Command', <Command key="cmd" text={s} />],
    [
      'Notice',
      <Notice key="no" title={s}>
        {s}
      </Notice>,
    ],
    [
      'EmptyState',
      <EmptyState key="e" title={s} command={s}>
        {s}
      </EmptyState>,
    ],
    [
      'Panel',
      <Panel key="p" title={s}>
        {s}
      </Panel>,
    ],
    ['UserChip', <UserChip key="u" name={s} />],
    ['Evidence', <Evidence key="ev" title={s} meta={s} text={`E   ${s}\n> ${s}`} truncated={s} />],
    ['Evidence output', <Evidence key="eo" kind="output" text={s} notice={s} />],
    [
      'HistoryGrid',
      <HistoryGrid
        key="h"
        runs={[{ id: 'a', label: s, detail: s, href: '/r' }]}
        rows={[{ nodeid: `t.py::t[${s}]`, outcomes: ['failed'] }]}
      />,
    ],
    ['PhaseTimeline', <PhaseTimeline key="ph" phases={[{ name: s, seconds: 1 }]} />],
    ['RecordingGuide', <RecordingGuide key="r" project={s} server={s} />],
    [
      'DataTable',
      <DataTable key="d" columns={[{ key: 'v', label: s }]} rows={[{ v: s }]} caption={s} />,
    ],
    [
      'RunList',
      <RunList
        key="rl"
        runs={[
          { id: 'a'.repeat(32), label: s, branch: s, sha: s, by: { name: s }, results: ['passed'] },
        ]}
      />,
    ],
    [
      'AppBar',
      <AppBar
        key="a"
        search={false}
        project={{ name: s, role: null }}
        projects={[{ name: s, role: null }]}
        nav={[{ id: 'runs', label: s }]}
        user={{ name: s }}
        account={{ note: s, items: [{ label: s, onSelect: () => undefined }] }}
        accountOpen
        switcherOpen
      />,
    ],
  ];
}

describe.each(PAYLOADS)('hostile text %s', (payload) => {
  it.each(cases(payload))('%s prints it as text', (_name, element) => {
    const { container } = render(element);
    expect(container.querySelector('img, script, iframe')).toBeNull();
    expect(container.textContent).toContain(payload);
  });
});
