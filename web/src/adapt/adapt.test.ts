import { describe, expect, it } from 'vitest';
import type { Project, ResultItem, RunDetail, RunListItem, RunMetadata } from '../api/queries';
import {
  dotlineLabel,
  metaItems,
  NOT_RECORDED,
  outcomesToResults,
  projectRef,
  resultRow,
  runHead,
  runItem,
} from './index';

const ID = '0123abcd0123abcd0123abcd0123abcd';
const COUNTS = { passed: 208, failed: 2, error: 1, skipped: 3, xfailed: 0, xpassed: 0 };

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
    expect(runItem(ITEM, { sessionUser: null, outcomes: '.FE' }).results).toEqual([
      'passed',
      'failed',
      'error',
    ]);
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
    expect(head.status).toEqual({ state: 'interrupted', exitStatus: 2, explain: true });
    expect(head.reason).toBe('KeyboardInterrupt');
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
    expect(head.reason).toBeNull();
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
    expect(resultRow(RESULT, 0)).toEqual({
      key: '0:tests/test_a.py::test_one',
      nodeId: 'tests/test_a.py::test_one',
      outcome: 'failed',
      message: 'AssertionError: expected 3.3V',
      seconds: 0.25,
    });
  });

  it('says failure text was not recorded when it was not', () => {
    const row = resultRow({ ...RESULT, outcome: 'error', failure: null }, 1);
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
    );
    expect(skipped.message).toBe('no rig');
    expect(resultRow({ ...RESULT, outcome: 'passed', failure: null }, 3).message).toBeNull();
  });
});
