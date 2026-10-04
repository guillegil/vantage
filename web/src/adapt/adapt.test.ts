import { describe, expect, it } from 'vitest';
import type {
  HistoryEntry,
  Project,
  ResultDetail,
  ResultItem,
  RunDetail,
  RunListItem,
  RunMetadata,
} from '../api/queries';
import {
  type BaselineWords,
  baselineLine,
  CHANGE,
  dotlineLabel,
  earlier,
  failingPhase,
  historyHref,
  historyRow,
  historyStrip,
  KEPT,
  lineResults,
  metaItems,
  NOT_RECORDED,
  outcomesToResults,
  projectRef,
  resultEvidence,
  resultHref,
  resultPhases,
  resultRow,
  runHead,
  runItem,
} from './index';

const ID = '0123abcd0123abcd0123abcd0123abcd';
const COUNTS = { passed: 208, failed: 2, error: 1, skipped: 3, xfailed: 0, xpassed: 0 };

const BASE = '1adf29af1adf29af1adf29af1adf29af';
const OTHER_BASE = '0b77e4f20b77e4f20b77e4f20b77e4f2';
const CHANGE_COUNTS = {
  new_failure: 2,
  still_failing: 3,
  fixed: 1,
  new_test: 4,
  removed: 1,
  not_reached: 0,
};

const ITEM: RunListItem = {
  id: ID,
  started_at: '2026-09-27T09:00:00Z',
  finished_at: '2026-09-27T09:02:12.400Z',
  exit_status: 1,
  interrupted: false,
  presentation: 'finished',
  vcs: {
    commit: '7f3a2c1e9b0d',
    branch: 'main',
    commit_subject: 'Fix',
    commit_subject_truncated: false,
    dirty: true,
  },
  recorded_by: 'alice',
  counts: COUNTS,
  comparison: { state: 'none', baseline: null, counts: null },
};

describe('runItem', () => {
  it('maps a run list item onto a run row', () => {
    expect(runItem(ITEM, { sessionUser: 'alice' })).toEqual({
      id: ID,
      label: '0123abcd',
      href: `/runs/${ID}`,
      state: 'finished',
      exitStatus: 1,
      counts: COUNTS,
      seconds: 132.4,
      branch: 'main',
      sha: '7f3a2c1e9b0d',
      dirty: true,
      by: { name: 'alice', you: true },
      startedAt: '2026-09-27T09:00:00Z',
      visibility: 'project',
    });
  });

  it('leaves out what was not recorded, and marks another user as not you', () => {
    const run = runItem(
      {
        ...ITEM,
        vcs: null,
        recorded_by: null,
        finished_at: null,
        exit_status: null,
        presentation: 'abandoned',
      },
      { sessionUser: 'bob' },
    );
    expect(run.state).toBe('abandoned');
    expect(run).not.toHaveProperty('branch');
    expect(run).not.toHaveProperty('by');
    expect(run).not.toHaveProperty('seconds');
    expect(run).not.toHaveProperty('exitStatus');
    expect(runItem(ITEM, { sessionUser: 'bob' }).by).toEqual({ name: 'alice', you: false });
  });

  it('carries the results once the outcomes are read', () => {
    expect(
      runItem(ITEM, { sessionUser: null, outcomes: { outcomes: '.FE', changes: null } }).results,
    ).toEqual(['passed', 'failed', 'error']);
  });

  it('carries each result’s change once a compared run’s outcomes are read', () => {
    expect(
      runItem(ITEM, { sessionUser: null, outcomes: { outcomes: '.FE', changes: 'f-n' } }).results,
    ).toEqual([
      { outcome: 'passed', change: 'fixed' },
      'failed',
      { outcome: 'error', change: 'new-failure' },
    ]);
  });

  it('counts new failures and fixes against the baseline, by its label, once compared', () => {
    const compared: RunListItem = {
      ...ITEM,
      comparison: {
        state: 'branch',
        baseline: { id: BASE, started_at: '2026-09-27T08:18:00Z', branch: 'main' },
        counts: CHANGE_COUNTS,
      },
    };
    expect(runItem(compared, { sessionUser: null }).changes).toEqual({
      newFailures: 2,
      fixed: 1,
      baseline: '1adf29af',
    });
    expect(
      runItem(
        { ...compared, comparison: { ...compared.comparison, state: 'project' } },
        {
          sessionUser: null,
        },
      ).changes,
    ).toEqual({ newFailures: 2, fixed: 1, baseline: '1adf29af' });
  });

  it('has no changes for a run pending its comparison or with nothing to compare with', () => {
    expect(runItem(ITEM, { sessionUser: null })).not.toHaveProperty('changes');
    const pending: RunListItem = {
      ...ITEM,
      exit_status: null,
      finished_at: null,
      presentation: 'running',
      comparison: { state: 'pending', baseline: null, counts: null },
    };
    expect(runItem(pending, { sessionUser: null })).not.toHaveProperty('changes');
  });
});

describe('changes', () => {
  it('spells each of the API’s change words as the design system does', () => {
    expect(CHANGE).toEqual({
      new_failure: 'new-failure',
      still_failing: 'still-failing',
      fixed: 'fixed',
      new_test: 'new-test',
      removed: 'removed',
      not_reached: 'not-reached',
    });
  });

  it('lines each outcome up with its change character, leaving unchanged results plain', () => {
    expect(lineResults('F.FsX', 'ns-tf')).toEqual([
      { outcome: 'failed', change: 'new-failure' },
      { outcome: 'passed', change: 'still-failing' },
      'failed',
      { outcome: 'skipped', change: 'new-test' },
      { outcome: 'xpassed', change: 'fixed' },
    ]);
  });

  it('draws plain outcomes for a run that was not compared', () => {
    expect(lineResults('.F', null)).toEqual(['passed', 'failed']);
    expect(lineResults('', null)).toEqual([]);
    expect(lineResults('', '')).toEqual([]);
  });

  it('keeps a change on its own result past a character it does not know', () => {
    expect(lineResults('.?F', '--n')).toEqual([
      'passed',
      { outcome: 'failed', change: 'new-failure' },
    ]);
  });
});

describe('baselineLine', () => {
  const RUN: RunDetail = {
    ...ITEM,
    started_at: '2026-09-27T09:00:00Z',
    interrupt_reason: null,
    project: 'firmware',
    comparison: {
      state: 'branch',
      baseline: { id: BASE, started_at: '2026-09-27T08:18:00Z', branch: 'main' },
      counts: CHANGE_COUNTS,
    },
  };
  const VCS = ITEM.vcs as NonNullable<RunListItem['vcs']>;
  const project = (vcs: RunDetail['vcs'], branch: string | null = 'main') => ({
    ...RUN,
    vcs,
    comparison: {
      state: 'project' as const,
      baseline: { id: OTHER_BASE, started_at: '2026-09-27T06:00:00Z', branch },
      counts: CHANGE_COUNTS,
    },
  });
  // Each recorded name in braces, the baseline's label in brackets.
  const words = (w: BaselineWords) =>
    w.map((p) => (typeof p === 'string' ? p : `{${p.recorded}}`)).join('');
  const said = (line: ReturnType<typeof baselineLine>) =>
    `${words(line.lead)}${line.baseline ? `[${line.baseline.label}]` : ''}${words(line.tail)}`;

  it('names the baseline on the run’s own branch, and how much earlier it started', () => {
    const line = baselineLine(RUN);
    expect(line.baseline).toEqual({ id: BASE, label: '1adf29af', href: `/runs/${BASE}` });
    expect(said(line)).toBe('Compared with [1adf29af] on {main}, 42 min earlier');
  });

  it('says why it fell back to the project’s latest complete run', () => {
    expect(said(baselineLine(project({ ...VCS, branch: 'feat/uart-dma' })))).toBe(
      'No earlier complete run on {feat/uart-dma}; compared with [0b77e4f2] on {main}, 3 h earlier',
    );
    expect(said(baselineLine(project({ ...VCS, branch: null, commit: '4b8f6a3c2d' })))).toBe(
      'No branch recorded (detached HEAD at {4b8f6a3}); compared with [0b77e4f2] on {main}, 3 h earlier',
    );
    expect(said(baselineLine(project(null)))).toBe(
      'Recorded outside a git repository; compared with [0b77e4f2] on {main}, 3 h earlier',
    );
    expect(said(baselineLine(project({ ...VCS, branch: null, commit: null })))).toBe(
      'No branch recorded; compared with [0b77e4f2] on {main}, 3 h earlier',
    );
  });

  it('names no branch for a baseline that recorded none', () => {
    expect(said(baselineLine(project({ ...VCS, branch: 'feat/uart-dma' }, null)))).toBe(
      'No earlier complete run on {feat/uart-dma}; compared with [0b77e4f2], 3 h earlier',
    );
  });

  it('keeps each recorded branch and commit apart from the words, as recorded', () => {
    const rlo = String.fromCodePoint(0x202e);
    expect(said(baselineLine(project({ ...VCS, branch: `x${rlo}` }, `y${rlo}`)))).toBe(
      `No earlier complete run on {x${rlo}}; compared with [0b77e4f2] on {y${rlo}}, 3 h earlier`,
    );
    expect(said(baselineLine(project({ ...VCS, branch: null, commit: `${rlo}ab12cd99` })))).toBe(
      `No branch recorded (detached HEAD at {${rlo}ab12cd}); compared with [0b77e4f2] on {main}, 3 h earlier`,
    );
  });

  it('shortens a recorded commit by character, never splitting one', () => {
    const face = String.fromCodePoint(0x1f600);
    const line = baselineLine(project({ ...VCS, branch: null, commit: `abcdef${face}99` }));
    expect(line.lead).toContainEqual({ recorded: `abcdef${face}` });
  });

  it('says there is nothing to compare with, or when a pending run is compared', () => {
    const none = { ...RUN, comparison: { state: 'none' as const, baseline: null, counts: null } };
    expect(baselineLine(none)).toEqual({
      lead: ['Nothing to compare with yet.'],
      baseline: null,
      tail: [],
    });
    const pending = {
      ...RUN,
      exit_status: null,
      finished_at: null,
      comparison: { state: 'pending' as const, baseline: null, counts: null },
    };
    expect(said(baselineLine({ ...pending, presentation: 'running' }))).toBe(
      'Compared with its baseline once the session ends.',
    );
    expect(said(baselineLine({ ...pending, presentation: 'abandoned' }))).toBe(
      'Not compared: no end was recorded.',
    );
  });

  it('measures how much earlier from the two start times', () => {
    expect(earlier('2026-09-27T09:00:00Z', '2026-09-27T09:00:00Z')).toBe('under 1 s');
    expect(earlier('2026-09-27T09:00:00Z', '2026-09-27T09:00:00.900Z')).toBe('under 1 s');
    expect(earlier('2026-09-27T09:00:00Z', '2026-09-27T09:00:12.600Z')).toBe('12 s');
    expect(earlier('2026-09-27T09:00:00Z', '2026-09-27T09:42:59Z')).toBe('42 min');
    expect(earlier('2026-09-27T06:00:00Z', '2026-09-27T09:59:00Z')).toBe('3 h');
    expect(earlier('2026-09-25T09:00:00Z', '2026-09-27T09:00:00Z')).toBe('2 d');
  });
});

it('reads pytest characters back into outcome words, in order', () => {
  expect(outcomesToResults('.FEsxX')).toEqual([
    'passed',
    'failed',
    'error',
    'skipped',
    'xfailed',
    'xpassed',
  ]);
  expect(outcomesToResults('')).toEqual([]);
});

it('labels the line by the order pytest reported the results', () => {
  expect(dotlineLabel(COUNTS, false)).toBe(
    'Results in the order pytest reported them: 2 failed, 208 passed, 3 skipped, 1 error',
  );
  expect(dotlineLabel({ passed: 0 }, true)).toBe(
    'Results in the order pytest reported them: no tests ran, still running',
  );
});

it('counts in the line’s label the changes its marks show', () => {
  expect(dotlineLabel(COUNTS, false, CHANGE_COUNTS)).toBe(
    'Results in the order pytest reported them: 2 failed, 208 passed, 3 skipped, 1 error; 2 new failures, 1 fixed',
  );
  expect(dotlineLabel(COUNTS, false, { ...CHANGE_COUNTS, new_failure: 0 })).toBe(
    'Results in the order pytest reported them: 2 failed, 208 passed, 3 skipped, 1 error; 1 fixed',
  );
  expect(dotlineLabel(COUNTS, false, { ...CHANGE_COUNTS, new_failure: 0, fixed: 0 })).toBe(
    'Results in the order pytest reported them: 2 failed, 208 passed, 3 skipped, 1 error',
  );
});

it('keeps a null role, as an open server answers', () => {
  const project: Project = { name: 'default', created_at: '2026-09-27T09:00:00Z', role: null };
  expect(projectRef(project)).toEqual({ name: 'default', role: null });
});

describe('runHead', () => {
  const DETAIL: RunDetail = {
    ...ITEM,
    interrupt_reason: 'KeyboardInterrupt',
    project: 'firmware',
    presentation: 'interrupted',
    interrupted: true,
    exit_status: 2,
  };

  it('builds the status, the commit with its subject, and the times', () => {
    const head = runHead(DETAIL);
    expect(head.label).toBe('0123abcd');
    expect(head.status).toEqual({
      state: 'interrupted',
      exitStatus: 2,
      explain: true,
      reason: 'KeyboardInterrupt',
    });
    expect(head.commit).toEqual({
      branch: 'main',
      sha: '7f3a2c1e9b0d',
      dirty: true,
      subject: 'Fix',
    });
    expect(head.seconds).toBeCloseTo(132.4);
    expect(head.running).toBe(false);
  });

  it('has no commit for a run outside git, and no end while running', () => {
    const head = runHead({
      ...DETAIL,
      vcs: null,
      presentation: 'running',
      finished_at: null,
      interrupted: false,
    });
    expect(head.commit).toEqual({});
    expect(head.status).not.toHaveProperty('reason');
    expect(head.seconds).toBeUndefined();
    expect(head.running).toBe(true);
  });
});

it('explains a declared key that has no value', () => {
  const metadata: RunMetadata = {
    items: [
      {
        key: 'rig',
        name: null,
        value: 'bench-02',
        status: 'captured',
        source: 'session',
        source_file: null,
        declared: false,
      },
      {
        key: 'os',
        name: null,
        value: null,
        status: 'source_unavailable',
        source: 'file',
        source_file: 'meta.json',
        declared: true,
      },
    ],
    files: [],
  };
  expect(metaItems(metadata)).toEqual([
    { key: 'rig', value: 'bench-02' },
    { key: 'os', value: null, source: 'file not read' },
  ]);
});

describe('resultRow', () => {
  const RESULT: ResultItem = {
    node_id: 'tests/test_a.py::test_one',
    file_path: 'tests/test_a.py',
    class_name: null,
    function_name: 'test_one',
    param_id: null,
    outcome: 'failed',
    duration: 0.25,
    started_at: null,
    finished_at: null,
    setup_outcome: null,
    call_outcome: null,
    teardown_outcome: null,
    setup_duration: null,
    call_duration: null,
    teardown_duration: null,
    worker_id: null,
    failure: {
      failure_type: 'AssertionError',
      failure_message: 'AssertionError: expected 3.3V\nassert 3.38 == 3.3',
      failure_message_truncated: false,
      failure_path: null,
      failure_lineno: null,
      skip_reason: null,
      xfail_reason: null,
    },
  };

  it('takes the first line of the failure message', () => {
    expect(resultRow(RESULT, 0, ID)).toEqual({
      key: '0:tests/test_a.py::test_one',
      nodeId: 'tests/test_a.py::test_one',
      href: `/runs/${ID}/result?node_id=tests%2Ftest_a.py%3A%3Atest_one`,
      outcome: 'failed',
      message: 'AssertionError: expected 3.3V',
      seconds: 0.25,
    });
  });

  it('says failure text was not recorded when it was not', () => {
    const row = resultRow({ ...RESULT, outcome: 'error', failure: null }, 1, ID);
    expect(row.message).toBe(NOT_RECORDED);
  });

  it('gives a skip its reason, and a pass nothing', () => {
    const skipped = resultRow(
      {
        ...RESULT,
        outcome: 'skipped',
        failure: {
          ...(RESULT.failure as NonNullable<ResultItem['failure']>),
          failure_message: null,
          skip_reason: 'no rig',
        },
      },
      2,
      ID,
    );
    expect(skipped.message).toBe('no rig');
    expect(resultRow({ ...RESULT, outcome: 'passed', failure: null }, 3, ID).message).toBeNull();
  });
});

describe('addresses', () => {
  it('carries a node id in the query string, encoded whole', () => {
    const node = 'tests/a b.py::T::test_x[a/b&c=d#e?]';
    const href = resultHref(ID, node);
    expect(href.startsWith(`/runs/${ID}/result?node_id=`)).toBe(true);
    expect(new URL(href, 'http://h').searchParams.get('node_id')).toBe(node);
    const history = historyHref('fw/x', node);
    expect(history.startsWith('/p/fw%2Fx/tests/history?node_id=')).toBe(true);
    expect(new URL(history, 'http://h').searchParams.get('node_id')).toBe(node);
  });
});

const DETAIL: ResultDetail = {
  node_id: 'tests/test_a.py::test_one',
  file_path: 'tests/test_a.py',
  class_name: null,
  function_name: 'test_one',
  param_id: null,
  outcome: 'failed',
  duration: 7.4,
  started_at: '2026-09-27T09:00:00Z',
  finished_at: '2026-09-27T09:00:07.400Z',
  setup_outcome: 'passed',
  call_outcome: 'failed',
  teardown_outcome: 'passed',
  setup_duration: 0.9,
  call_duration: 6.2,
  teardown_duration: 0.3,
  worker_id: null,
  failure_type: 'AssertionError',
  failure_message: 'AssertionError: expected 3.3V',
  failure_message_truncated: false,
  failure_path: 'tests/test_a.py',
  failure_lineno: 12,
  failure_repr: "AssertionError('expected 3.3V')",
  failure_repr_truncated: false,
  traceback: 'E   AssertionError',
  traceback_truncated: true,
  skip_reason: null,
  skip_reason_truncated: false,
  xfail_reason: null,
  xfail_reason_truncated: false,
  captured_stdout: 'hello\n',
  captured_stdout_truncated: false,
  captured_stderr: null,
  captured_stderr_truncated: true,
  position: 0,
  change: null,
  was: null,
  streak: null,
};

const NOTHING: Partial<ResultDetail> = {
  failure_type: null,
  failure_message: null,
  failure_path: null,
  failure_lineno: null,
  failure_repr: null,
  traceback: null,
  traceback_truncated: false,
  captured_stdout: null,
  captured_stderr: null,
  captured_stderr_truncated: false,
};

describe('resultEvidence', () => {
  it('lists what was recorded in reading order, with where it failed and what was cut', () => {
    const ev = resultEvidence(DETAIL);
    expect(ev.blocks).toEqual([
      {
        key: 'traceback',
        title: 'Traceback',
        text: 'E   AssertionError',
        kind: 'traceback',
        meta: 'call phase · tests/test_a.py:12',
        truncated: KEPT,
      },
      {
        key: 'message',
        title: 'Message',
        text: 'AssertionError: expected 3.3V',
        kind: 'output',
        meta: 'AssertionError',
      },
      {
        key: 'repr',
        title: 'Exception repr',
        text: "AssertionError('expected 3.3V')",
        kind: 'output',
      },
      {
        key: 'stdout',
        title: 'Captured stdout',
        text: 'hello\n',
        kind: 'output',
        meta: 'setup, call and teardown',
      },
    ]);
    expect(ev.dropped).toEqual(['Captured stderr']);
    expect(ev.absence).toBeNull();
  });

  it('gives a skip and an xfail their reasons', () => {
    const skipped = resultEvidence({
      ...DETAIL,
      ...NOTHING,
      outcome: 'skipped',
      skip_reason: 'Skipped: no rig',
    });
    expect(skipped.blocks.map((b) => [b.title, b.text])).toEqual([
      ['Skip reason', 'Skipped: no rig'],
    ]);
    const xfailed = resultEvidence({
      ...DETAIL,
      ...NOTHING,
      outcome: 'xfailed',
      xfail_reason: 'known drift',
      xfail_reason_truncated: true,
    });
    expect(xfailed.blocks).toEqual([
      { key: 'xfail', title: 'Xfail reason', text: 'known drift', kind: 'output', truncated: KEPT },
    ]);
  });

  it('knows failure text was not recorded when a failure holds none', () => {
    const ev = resultEvidence({ ...DETAIL, ...NOTHING });
    expect(ev).toEqual({ blocks: [], dropped: [], absence: 'unrecorded' });
    expect(resultEvidence({ ...DETAIL, ...NOTHING, outcome: 'error' }).absence).toBe('unrecorded');
  });

  it('never claims failure text was not recorded where output capture may have been off', () => {
    // A pass recorded with --vantage-failure-text under -s holds every field null too.
    expect(resultEvidence({ ...DETAIL, ...NOTHING, outcome: 'passed' })).toEqual({
      blocks: [],
      dropped: [],
      absence: 'unknown',
    });
    // A bare xfail under -s: its reason recorded empty, its output not at all.
    expect(
      resultEvidence({ ...DETAIL, ...NOTHING, outcome: 'xfailed', xfail_reason: '' }).absence,
    ).toBe('unknown');
  });

  it('knows output was captured and empty', () => {
    const ev = resultEvidence({
      ...DETAIL,
      ...NOTHING,
      outcome: 'passed',
      captured_stdout: '',
      captured_stderr: '',
    });
    expect(ev).toEqual({ blocks: [], dropped: [], absence: 'silent' });
    expect(
      resultEvidence({
        ...DETAIL,
        ...NOTHING,
        outcome: 'xfailed',
        xfail_reason: '',
        captured_stdout: '',
      }).absence,
    ).toBe('silent');
  });
});

describe('phases', () => {
  it('finds the phase that failed', () => {
    expect(failingPhase(DETAIL)).toBe('call');
    expect(failingPhase({ ...DETAIL, setup_outcome: 'failed', call_outcome: null })).toBe('setup');
    expect(failingPhase({ ...DETAIL, call_outcome: 'passed' })).toBeNull();
  });

  it('draws each phase that ran, naming what did not pass as the result saw it', () => {
    expect(resultPhases(DETAIL)).toEqual([
      { name: 'setup', seconds: 0.9 },
      { name: 'call', seconds: 6.2, outcome: 'failed' },
      { name: 'teardown', seconds: 0.3 },
    ]);
    expect(
      resultPhases({
        ...DETAIL,
        outcome: 'error',
        setup_outcome: 'failed',
        call_outcome: null,
        call_duration: null,
      }),
    ).toEqual([
      { name: 'setup', seconds: 0.9, outcome: 'error' },
      { name: 'teardown', seconds: 0.3 },
    ]);
    expect(resultPhases({ ...DETAIL, outcome: 'xfailed', call_outcome: 'skipped' })[1]).toEqual({
      name: 'call',
      seconds: 6.2,
      outcome: 'xfailed',
    });
    expect(resultPhases({ ...DETAIL, outcome: 'error', teardown_outcome: 'failed' })[2]).toEqual({
      name: 'teardown',
      seconds: 0.3,
      outcome: 'error',
    });
    expect(
      resultPhases({
        ...DETAIL,
        setup_duration: null,
        call_duration: null,
        teardown_duration: null,
      }),
    ).toEqual([]);
  });
});

describe('history', () => {
  const OLD = 'aaaaaaaa'.repeat(4);
  const NEW = 'bbbbbbbb'.repeat(4);
  const NODE = 'tests/test_a.py::test_one';
  const ENTRIES: HistoryEntry[] = [
    {
      run_id: NEW,
      started_at: '2026-09-27T10:00:00Z',
      finished_at: '2026-09-27T10:01:00Z',
      outcome: 'failed',
      duration: 1.5,
      vcs: {
        commit: '7aa1c5d9e0',
        branch: 'main',
        commit_subject: 'Fix',
        commit_subject_truncated: false,
        dirty: true,
      },
      change: 'new_failure',
    },
    {
      run_id: OLD,
      started_at: '2026-09-27T09:00:00Z',
      finished_at: null,
      outcome: 'passed',
      duration: null,
      vcs: null,
      change: null,
    },
  ];

  it('turns a page, newest first, into a strip, oldest first, each run linking its result', () => {
    expect(historyStrip(ENTRIES, NODE)).toEqual({
      runs: [
        { id: OLD, label: 'aaaaaaaa', href: resultHref(OLD, NODE) },
        { id: NEW, label: 'bbbbbbbb', detail: 'main at 7aa1c5d', href: resultHref(NEW, NODE) },
      ],
      outcomes: ['passed', 'failed'],
      changes: [null, 'new-failure'],
      durations: [null, 1.5],
    });
  });

  it('names a run by its branch or its commit alone when that is all it has', () => {
    const vcs = ENTRIES[0]?.vcs as NonNullable<HistoryEntry['vcs']>;
    const only = (v: Partial<typeof vcs>) =>
      historyStrip([{ ...(ENTRIES[0] as HistoryEntry), vcs: { ...vcs, ...v } }], NODE).runs[0]
        ?.detail;
    expect(only({ commit: null })).toBe('main');
    expect(only({ branch: null })).toBe('7aa1c5d');
    expect(only({ branch: null, commit: null })).toBeUndefined();
  });

  it('hands a branch name over as recorded, for the grid to write out', () => {
    const vcs = ENTRIES[0]?.vcs as NonNullable<HistoryEntry['vcs']>;
    const [run] = historyStrip(
      [
        {
          ...(ENTRIES[0] as HistoryEntry),
          vcs: { ...vcs, branch: `x${String.fromCodePoint(0x202e)}` },
        },
      ],
      NODE,
    ).runs;
    expect(run?.detail).toBe(`x${String.fromCodePoint(0x202e)} at 7aa1c5d`);
  });

  it('maps an entry onto a row of the history table', () => {
    expect(historyRow(ENTRIES[0] as HistoryEntry, NODE)).toEqual({
      key: NEW,
      runId: NEW,
      label: 'bbbbbbbb',
      href: resultHref(NEW, NODE),
      outcome: 'failed',
      commit: { branch: 'main', sha: '7aa1c5d9e0', dirty: true },
      startedAt: '2026-09-27T10:00:00Z',
      seconds: 1.5,
    });
    expect(historyRow(ENTRIES[1] as HistoryEntry, NODE).commit).toEqual({});
  });
});
