// Run by `tsc` (typecheck), never by Vitest: each ported component must be
// assignable to the type the design system declares for it, so a prop that
// drifts from the vendored contract fails the build.
import type * as Contract from './contract';
import {
  AppBar,
  BaselineNote,
  Button,
  ChangeBadge,
  Command,
  CommitRef,
  DataTable,
  Dotline,
  DurationSpark,
  EmptyState,
  Evidence,
  Field,
  HistoryGrid,
  Icon,
  MenuButton,
  MetaList,
  NodeId,
  Notice,
  OutcomeBadge,
  OutcomeMark,
  Pager,
  Panel,
  PhaseTimeline,
  PinButton,
  ProjectSwitcher,
  RecordingGuide,
  RerunButton,
  RetentionTag,
  RoleBadge,
  RunList,
  RunStatus,
  SegmentedControl,
  SummaryLine,
  Tabs,
  Time,
  Tooltip,
  UserChip,
  VisibilityTag,
  Wordmark,
} from './index';

export const ported: {
  [K in
    | 'AppBar'
    | 'BaselineNote'
    | 'Button'
    | 'ChangeBadge'
    | 'Command'
    | 'CommitRef'
    | 'DataTable'
    | 'Dotline'
    | 'DurationSpark'
    | 'EmptyState'
    | 'Evidence'
    | 'Field'
    | 'HistoryGrid'
    | 'Icon'
    | 'MenuButton'
    | 'MetaList'
    | 'NodeId'
    | 'Notice'
    | 'OutcomeBadge'
    | 'OutcomeMark'
    | 'Pager'
    | 'Panel'
    | 'PhaseTimeline'
    | 'PinButton'
    | 'ProjectSwitcher'
    | 'RecordingGuide'
    | 'RerunButton'
    | 'RetentionTag'
    | 'RoleBadge'
    | 'RunList'
    | 'RunStatus'
    | 'SegmentedControl'
    | 'SummaryLine'
    | 'Tabs'
    | 'Time'
    | 'Tooltip'
    | 'UserChip'
    | 'VisibilityTag'
    | 'Wordmark']: (typeof Contract)[K];
} = {
  AppBar,
  BaselineNote,
  Button,
  ChangeBadge,
  Command,
  CommitRef,
  DataTable,
  Dotline,
  DurationSpark,
  EmptyState,
  Evidence,
  Field,
  HistoryGrid,
  Icon,
  MenuButton,
  MetaList,
  NodeId,
  Notice,
  OutcomeBadge,
  OutcomeMark,
  Pager,
  Panel,
  PhaseTimeline,
  PinButton,
  ProjectSwitcher,
  RecordingGuide,
  RerunButton,
  RetentionTag,
  RoleBadge,
  RunList,
  RunStatus,
  SegmentedControl,
  SummaryLine,
  Tabs,
  Time,
  Tooltip,
  UserChip,
  VisibilityTag,
  Wordmark,
};
