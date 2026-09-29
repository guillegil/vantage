// Run by `tsc` (typecheck), never by Vitest: each ported component must be
// assignable to the type the design system declares for it, so a prop that
// drifts from the vendored contract fails the build.
import type * as Contract from './contract';
import {
  AppBar,
  Button,
  Command,
  CommitRef,
  DataTable,
  Dotline,
  EmptyState,
  Field,
  Icon,
  MenuButton,
  MetaList,
  NodeId,
  Notice,
  OutcomeBadge,
  OutcomeMark,
  Pager,
  Panel,
  PinButton,
  ProjectSwitcher,
  RecordingGuide,
  RetentionTag,
  RoleBadge,
  RunList,
  RunStatus,
  SummaryLine,
  Time,
  Tooltip,
  UserChip,
  VisibilityTag,
  Wordmark,
} from './index';

export const ported: {
  [K in
    | 'AppBar'
    | 'Button'
    | 'Command'
    | 'CommitRef'
    | 'DataTable'
    | 'Dotline'
    | 'EmptyState'
    | 'Field'
    | 'Icon'
    | 'MenuButton'
    | 'MetaList'
    | 'NodeId'
    | 'Notice'
    | 'OutcomeBadge'
    | 'OutcomeMark'
    | 'Pager'
    | 'Panel'
    | 'PinButton'
    | 'ProjectSwitcher'
    | 'RecordingGuide'
    | 'RetentionTag'
    | 'RoleBadge'
    | 'RunList'
    | 'RunStatus'
    | 'SummaryLine'
    | 'Time'
    | 'Tooltip'
    | 'UserChip'
    | 'VisibilityTag'
    | 'Wordmark']: (typeof Contract)[K];
} = {
  AppBar,
  Button,
  Command,
  CommitRef,
  DataTable,
  Dotline,
  EmptyState,
  Field,
  Icon,
  MenuButton,
  MetaList,
  NodeId,
  Notice,
  OutcomeBadge,
  OutcomeMark,
  Pager,
  Panel,
  PinButton,
  ProjectSwitcher,
  RecordingGuide,
  RetentionTag,
  RoleBadge,
  RunList,
  RunStatus,
  SummaryLine,
  Time,
  Tooltip,
  UserChip,
  VisibilityTag,
  Wordmark,
};
