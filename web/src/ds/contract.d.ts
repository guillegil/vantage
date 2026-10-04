import type * as React from 'react';

/** pytest's six test outcomes, in pytest's own lowercase words. */
export type Outcome = 'passed' | 'failed' | 'error' | 'skipped' | 'xfailed' | 'xpassed';
/** Counts keyed the way pytest's summary line names them. */
export interface OutcomeCounts { failed?: number; passed?: number; skipped?: number; deselected?: number; xfailed?: number; xpassed?: number; warnings?: number; error?: number }
export type RunState = 'running' | 'finished' | 'interrupted' | 'abandoned';
export type Role = 'owner' | 'editor' | 'viewer';
export type Visibility = 'private' | 'project';
/**
 * What a result did against its run's baseline (the previous finished run on the same branch, else the project's
 * previous run), as the server decides it. removed and not-reached describe baseline tests this run lacks.
 */
export type Change = 'new-failure' | 'still-failing' | 'fixed' | 'new-test' | 'removed' | 'not-reached';
export type IconName =
  | 'check' | 'cross' | 'dash' | 'alert' | 'circle-cross' | 'circle-check' | 'pin' | 'lock' | 'users' | 'user' | 'link' | 'eye'
  | 'pencil' | 'key' | 'chevron-down' | 'chevron-right' | 'chevron-left' | 'chevron-up' | 'circle-minus' | 'search' | 'filter' | 'copy' | 'download' | 'clock' | 'branch' | 'commit'
  | 'square' | 'trash' | 'plus' | 'external' | 'sliders' | 'log-out' | 'archive' | 'plug' | 'flask' | 'terminal'
  | 'info' | 'warning' | 'more' | 'menu' | 'play'
  | 'chevrons' | 'chart' | 'table' | 'image' | 'file' | 'file-text' | 'zoom-in' | 'zoom-out' | 'fit'
  | 'list' | 'columns' | 'chart-bar' | 'chart-scatter' | 'shield';

// Foundations
export interface IconProps { name: IconName; size?: number; title?: string; className?: string }
export declare function Icon(props: IconProps): React.ReactElement;
export interface WordmarkProps { href?: string; className?: string }
export declare function Wordmark(props: WordmarkProps): React.ReactElement;

// Outcomes
export interface OutcomeMarkProps { outcome: Outcome; size?: number; label?: string; className?: string }
export declare function OutcomeMark(props: OutcomeMarkProps): React.ReactElement;
export interface OutcomeBadgeProps { outcome: Outcome; count?: number; className?: string }
export declare function OutcomeBadge(props: OutcomeBadgeProps): React.ReactElement;
/** An outcome, or a result with its change against the run's baseline: a new failure stands above the track, a fixed result wears a ring. */
export type ResultLike = Outcome | { outcome: Outcome; nodeid?: string; change?: Change | null };
export interface DotlineProps {
  /** Results in collection order. */
  results?: ResultLike[];
  /** 'marks' (default): the height-coded strip. 'chars': pytest's progress characters. */
  variant?: 'marks' | 'chars';
  /** For 'chars': one row per file, as pytest prints them. */
  files?: { path: string; results: ResultLike[] }[];
  /** For 'chars' while running: the collected total, so percentages are progress. */
  total?: number;
  /** Width in px the strip may use; beyond it, results share marks and a caption says how many. 'auto' takes the component's own width and redraws as it resizes. */
  width?: number | 'auto';
  /** Content set to the line's own measure, such as pytest's SummaryLine under a run's line. */
  under?: React.ReactNode;
  /** A result's index: its mark is outlined in the focus colour, for the selected test's place in the run. */
  cursor?: number;
  running?: boolean;
  showScale?: boolean;
  label?: string;
  className?: string;
}
export declare function Dotline(props: DotlineProps): React.ReactElement;
export interface SummaryLineProps { counts: OutcomeCounts; seconds?: number; running?: boolean; className?: string }
export declare function SummaryLine(props: SummaryLineProps): React.ReactElement;
export interface RunStatusProps {
  state?: RunState;
  exitStatus?: 0 | 1 | 2 | 3 | 4 | 5 | number;
  lastContact?: string;
  /** Mark only; the words and the reason go to the tooltip and screen readers. */
  compact?: boolean;
  /** Print why a run is interrupted or abandoned, not only for screen readers: for run page heads. */
  explain?: boolean;
  /** What the report said stopped the session, as recorded (pytest's words, such as KeyboardInterrupt). Hidden characters show as their code points. */
  reason?: string | null;
  className?: string;
}
export declare function RunStatus(props: RunStatusProps): React.ReactElement;

// History
export interface HistoryGridProps {
  /** Columns, oldest first. label is the run's label, as lists print it (the first 8 characters of its id); detail, such as the branch and commit, joins it in the readout. href, the address of this test's result in that run, makes the cell open it. */
  runs: { id: string; label: string; detail?: string; href?: string }[];
  /** One row per test; outcomes line up with runs, null where the test was not in that run. */
  rows: {
    nodeid: string;
    outcomes: (Outcome | null)[];
    /** Aligned to outcomes: each result's change against its run's baseline, from the server. Draws change marks and joins the readout. */
    changes?: (Change | null)[];
    /** false leaves the note out of sight (screen readers still hear it) where the line above already says it. */
    note?: boolean;
    href?: string;
  }[];
  /** Drop the label column and put the note under the marks, for a side column. */
  stacked?: boolean;
  /** With stacked: the runs take the grid's width, up to 28px a run with 24px marks, for a test's own page. */
  fill?: boolean;
  /** Follows a run's href in place of loading it as a new page: a click opens the run under the pointer, Enter the run under the cursor. */
  onOpen?: (href: string, run: { id: string; label: string; detail?: string; href?: string }) => void;
  className?: string;
}
export declare function HistoryGrid(props: HistoryGridProps): React.ReactElement;
export interface DurationSparkProps { values: (number | null)[]; width?: number; height?: number; showRange?: boolean; className?: string }
export declare function DurationSpark(props: DurationSparkProps): React.ReactElement;

// Data
export interface NodeIdProps { value: string; truncate?: boolean; size?: 'md' | 'lg'; href?: string; className?: string }
export declare function NodeId(props: NodeIdProps): React.ReactElement;
export interface CommitRefProps { branch?: string; sha?: string; dirty?: boolean; subject?: string; className?: string }
export declare function CommitRef(props: CommitRefProps): React.ReactElement;
export interface TimeProps { value: string | Date; mode?: 'relative' | 'absolute' | 'date'; now?: string | Date; className?: string }
export declare function Time(props: TimeProps): React.ReactElement;
export interface MetaListProps { items: { key: string; value: string | number | null; source?: string }[]; emptyText?: string; className?: string }
export declare function MetaList(props: MetaListProps): React.ReactElement;
export interface DataTableColumn<Row> {
  key: string;
  label: React.ReactNode;
  /** 'num' right-aligns in tabular mono; a column with a numeric format does so unless told otherwise. */
  align?: 'start' | 'num' | 'end';
  width?: number | string;
  render?: (row: Row) => React.ReactNode;
  /** Formats the raw value when there is no render. */
  format?: ValueFormat;
  unit?: string;
  sortable?: boolean;
  /** What the column sorts by when the cell renders something else. Empty values sort last either way. */
  sortValue?: (row: Row) => number | string | null | undefined;
  /** The first press's order. Default: largest first for figures, A to Z for words. */
  defaultDir?: 'asc' | 'desc';
}
/** How a column's values print: counts grouped, seconds as durations, bytes in IEC units, percent of 100. */
export type ValueFormat = 'count' | 'seconds' | 'bytes' | 'percent' | 'number' | 'text' | ((value: number) => string);
export interface DataTableSort { key: string; dir: 'asc' | 'desc' }
export interface DataTableProps<Row> {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  /** The row field that keys it; also what selected, selectedKeys and onRowSelect name. */
  rowKey?: keyof Row & string;
  /** A row to mark as the one open (accent-soft). */
  selected?: unknown;
  /** Makes each row open something; its first cell becomes a button. Keep that cell free of links. */
  onRowSelect?: (row: Row) => void;
  /** 'multiple' adds a checkbox column with select-all. */
  selectable?: 'multiple';
  selectedKeys?: unknown[];
  defaultSelectedKeys?: unknown[];
  onSelectionChange?: (keys: unknown[]) => void;
  /** Names a row for its checkbox: "Select tests/...". Default: its key. */
  rowLabel?: (row: Row) => string;
  sort?: DataTableSort | null;
  defaultSort?: DataTableSort | null;
  onSortChange?: (sort: DataTableSort) => void;
  /** Scrolls in its own box of this height with the header stuck; past windowAfter rows only those in view are rendered. */
  maxHeight?: number | string;
  /** Default 100. */
  windowAfter?: number;
  /** Row nouns for the foot's count. Default "rows", "row". */
  noun?: string;
  nounOne?: string;
  /** Replaces the foot; null removes it. */
  footer?: React.ReactNode;
  caption?: string;
  dense?: boolean;
  empty?: React.ReactNode;
  className?: string;
}
export declare function DataTable<Row>(props: DataTableProps<Row>): React.ReactElement;
export interface EvidenceProps {
  title?: string;
  text: string;
  /** 'traceback' marks pytest's E, > and path.py:N: lines. */
  kind?: 'traceback' | 'output' | 'log';
  /** true, or a sentence saying what was kept: "Only the first 64 KiB was kept." */
  truncated?: boolean | string;
  notice?: React.ReactNode;
  /** Beside the title, such as the phase. A string is recorded text: hidden characters show as their code points. */
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  /** false hides the built-in Wrap lines, Expand and Copy controls. */
  controls?: boolean;
  /** Heading level of the title, 1 to 6. Default 3. */
  level?: number;
  className?: string;
}
export declare function Evidence(props: EvidenceProps): React.ReactElement;
export interface PhaseTimelineProps { phases: { name: 'setup' | 'call' | 'teardown' | string; seconds: number; outcome?: Outcome }[]; className?: string }
export declare function PhaseTimeline(props: PhaseTimelineProps): React.ReactElement;
export interface CommandProps { text?: string; children?: string; prompt?: boolean; /** false drops its copy button, where a stronger control beside it copies the same text. */ copy?: boolean; className?: string }
export declare function Command(props: CommandProps): React.ReactElement;
export interface RecordingGuideProps {
  project: string;
  /** The server's address as a person's machine reaches it, e.g. location.origin. Omitted from the command when it is the plugin's default. */
  server: string;
  /** An open server has no accounts: the token step goes. */
  open?: boolean;
  /** false when the person's role cannot record here (viewers): the guide says why instead of showing a command. */
  canRecord?: boolean;
  /** Replaces the default explanation shown when canRecord is false. */
  cannotRecord?: React.ReactNode;
  /** The guide's heading, or false inside an already titled panel. Default "Record a run into <project>". */
  title?: React.ReactNode | false;
  /** Heading level, 2 to 6. Default 3. */
  level?: number;
  className?: string;
}
export declare function RecordingGuide(props: RecordingGuideProps): React.ReactElement;

// Runs
export interface RunItem {
  /** The run's full id (32 hex characters from the API): the row's key, its link's tooltip. */
  id: string;
  /** What the list prints for the run: usually the id's first 8 characters. Absent, the full id is printed and truncates. */
  label?: string;
  href?: string;
  state?: RunState;
  exitStatus?: number;
  results?: ResultLike[];
  counts?: OutcomeCounts;
  seconds?: number;
  branch?: string;
  sha?: string;
  dirty?: boolean;
  by?: { name: string; initials?: string; you?: boolean };
  startedAt?: string;
  visibility?: Visibility;
  sharedWith?: number;
  pinned?: boolean;
  pinDisabledReason?: string;
  retention?: RetentionTagProps;
  /** Changes against the run's baseline, printed ahead of the outcome counts: "2 new · 1 fixed". baseline is its label. */
  changes?: { newFailures?: number; fixed?: number; baseline?: string };
}
export interface RunListProps {
  runs: RunItem[];
  /** false hides the column header row (it hides itself below 760px). */
  header?: boolean;
  now?: string | Date;
  /** Absent, no pin buttons render; the pin column stays as an empty cell so rows keep their grid. */
  onPin?: (run: RunItem, pinned: boolean) => void;
  empty?: React.ReactNode;
  label?: string;
  className?: string;
}
export declare function RunList(props: RunListProps): React.ReactElement;
export interface PinButtonProps { pinned?: boolean; defaultPinned?: boolean; onChange?: (pinned: boolean) => void; disabledReason?: string; className?: string }
export declare function PinButton(props: PinButtonProps): React.ReactElement;
export interface RetentionTagProps {
  state: 'kept' | 'pinned' | 'expires';
  days?: number;
  rule?: string;
  /** On a run's own page: the UTC date the rule deletes it, printed after the days. */
  date?: string | Date;
  title?: string;
  className?: string;
}
export declare function RetentionTag(props: RetentionTagProps): React.ReactElement;

// Change
export interface ChangeBadgeProps {
  change: Change;
  /** The baseline's outcome for this test: "was passed". */
  was?: Outcome | null;
  /** For a test still failing: how many runs in a row, and the label of the first. */
  streak?: { runs: number; since?: string };
  /** The baseline run's label, named by detail. */
  baseline?: string;
  /** This result's outcome, so a fixed test that was skipped draws a skipped mark. */
  outcome?: Outcome | null;
  /** Add the sentence a detail head needs: "New failure: passed in 1adf29af". */
  detail?: boolean;
  className?: string;
}
export declare function ChangeBadge(props: ChangeBadgeProps): React.ReactElement | null;
export interface QueueResult {
  nodeid: string;
  /** Views placed in the result list: a RecordChip each, after the change. */
  chips?: RecordChipProps[];
  /** null for a test this run lacks, when paged rows carry removed and not-reached tests. */
  outcome: Outcome | null;
  seconds?: number | null;
  change?: Change | null;
  was?: Outcome | null;
  streak?: { runs: number; since?: string };
  href?: string;
}
/** A test the baseline had and this run lacks: removed, or not reached when the run stopped early. */
export interface MissingResult { nodeid: string; was?: Outcome | null }
/** A queue group: one of the changes, or failures for a run with nothing to compare with. */
export type QueueGroup = Change | 'failures';
/** How many tests each group holds on the server, the six changes and the failures of a run with no baseline. */
export type QueueTotals = Partial<Record<QueueGroup, number>>;
export interface ChangeQueueDetailContext {
  /** The selected test's rerun command, null for a test this run lacks. */
  command: string | null;
  baseline: string | null;
  /** Position in what the queue shows, from 0; -1 when the selected test is filtered out. */
  index: number;
  count: number;
}
export interface ChangeQueueProps {
  /** This run's results in collection order; with totals, the changed tests (or failures) loaded so far, in queue order, removed and not-reached ones included. */
  results: QueueResult[];
  missing?: MissingResult[];
  /** The run was interrupted or abandoned: missing tests are "Not reached", never "Removed". */
  interrupted?: boolean;
  /** The baseline run; null when there is nothing to compare with. */
  baseline?: { label: string; href?: string } | null;
  /** Said when there is no baseline. Default "Nothing to compare with yet." */
  baselineNote?: React.ReactNode;
  /** The run's result count, for "All 214 results". Default results.length. */
  total?: number;
  /** The selected node id, when the page owns it (keep it in the address). Absent, the first row of the first group. */
  selected?: string;
  onSelect?: (result: QueueResult | (MissingResult & { change: Change })) => void;
  /** Enter on a row with two panes: open the result page. */
  onOpen?: (result: QueueResult | (MissingResult & { change: Change })) => void;
  /** Default: pytest and the node id, quoted for a POSIX shell where it needs it. */
  commandFor?: (result: QueueResult) => string;
  /** Given, the queue becomes the triage view: two panes from 900px of its own width, one at a time below. */
  renderDetail?: (result: QueueResult | (MissingResult & { change: Change; outcome: null }), context: ChangeQueueDetailContext) => React.ReactNode;
  /** Results still arriving: a polite live region counts new failures so far. */
  running?: boolean;
  /** j, k and c anywhere on the page, / to the filter; never inside a text field, a menu or a dialog. */
  shortcuts?: boolean;
  /** Rows per group before "Show N more". Default 50. */
  pageSize?: number;
  /** Rows per page in the whole-run view, and per "Show more" when paged. Default 200. */
  allPageSize?: number;
  /** Paged by the server: how many tests each group holds. A group shows when its total is not zero, loaded or not, and its head prints the total. Absent, the queue counts results itself, as it always has. */
  totals?: QueueTotals;
  /** Paged: called when "Show more" wants rows past those loaded, and when a group with none loaded is first opened. 'all' is the whole-run view. */
  onMore?: (group: QueueGroup | 'all') => void;
  /** Paged: the groups whose next rows are on their way; their "Show more" is busy. */
  loading?: (QueueGroup | 'all')[];
  /** Paged: the whole-run view's rows loaded so far, in collection order, already filtered by outcome. */
  allResults?: QueueResult[];
  /** Paged: the run's outcome counts, for the whole-run view's filter. */
  outcomeCounts?: OutcomeCounts;
  /** Paged: the whole-run view's outcome filter, which the server applies. */
  outcome?: Outcome | 'all';
  onOutcome?: (outcome: Outcome | 'all') => void;
  defaultView?: 'changes' | 'all';
  label?: string;
  className?: string;
}
export declare function ChangeQueue(props: ChangeQueueProps): React.ReactElement;
export interface BaselineNoteProps {
  /** compared: it has a baseline. none: nothing earlier was complete. pending: still running, compared once it ends. abandoned: no end was recorded. Default compared with a baseline, none without. */
  state?: 'compared' | 'none' | 'pending' | 'abandoned';
  /** The baseline run: its label, its address, its full id (the label's tooltip) and the branch it ran on. */
  baseline?: { label: string; href?: string; id?: string; branch?: string | null } | null;
  /** How much earlier the baseline started than this run, in seconds. */
  earlier?: number;
  /** Why the baseline is the project's latest complete run rather than one on this run's branch: no earlier complete run on its branch, a detached HEAD, git information without a branch or commit, or no git information at all. */
  fallback?: { reason: 'branch'; branch: string } | { reason: 'detached'; commit: string } | { reason: 'no-branch' } | { reason: 'no-git' } | null;
  /** An action that ends the line, such as the rerun of the new failures. */
  children?: React.ReactNode;
  className?: string;
}
export declare function BaselineNote(props: BaselineNoteProps): React.ReactElement;
export interface RerunButtonProps {
  nodeids: string[];
  /** The label; default "Copy rerun command". */
  children?: React.ReactNode;
  /** Past this many node ids it offers a file for pytest @file instead. Default 20. */
  limit?: number;
  /** The downloaded file's name. Default "rerun.txt". */
  fileName?: string;
  /** Replaces the generated command. */
  command?: string;
  /** 'primary' where rerunning is the page's action. Default 'secondary'. */
  variant?: 'primary' | 'secondary';
  /** The key that does the same, printed in the button and set as aria-keyshortcuts: 'c' on the run page. */
  keyHint?: string;
  size?: 'sm' | 'md';
  className?: string;
}
export declare function RerunButton(props: RerunButtonProps): React.ReactElement | null;

// Workspace
export interface ProjectRef {
  name: string;
  /** The viewer's role in the project; null on an open server, which checks no role and shows no badge. */
  role: Role | null;
  /** @deprecated Runs join a project by --vantage-project, not by metadata. Still printed under the name when given. */
  tracks?: string;
}
/** An item of the account menu: Account, Administration for an admin, a separator, Sign out. */
export type AccountMenuItem = { label: string; icon?: IconName; onSelect: () => void; current?: boolean } | { separator: true };
export interface AppBarAccount {
  /** First line of the menu, e.g. "Signed in as alice until 21:14 UTC". Absent, the user's name. */
  note?: React.ReactNode;
  items: AccountMenuItem[];
}
export interface AppBarProps {
  project?: ProjectRef;
  projects?: ProjectRef[];
  nav?: { id: string; label: string; href?: string }[];
  active?: string;
  /** The signed-in user. Absent (an open server has no accounts), no account button renders. */
  user?: { name: string; initials?: string };
  /** Makes the account button open a menu. With a user but no account, the monogram shows without a menu. */
  account?: AppBarAccount;
  /** Opens the account menu initially; for previews and docs. */
  accountOpen?: boolean;
  /** The project menu's heading, forwarded to ProjectSwitcher: "All projects" for an admin. */
  switcherLabel?: string;
  /** Forwarded to ProjectSwitcher: only server admins create projects. */
  canCreate?: boolean;
  onCreate?: () => void;
  search?: boolean;
  searchId?: string;
  switcherOpen?: boolean;
  homeHref?: string;
  onSelectProject?: (project: ProjectRef) => void;
  className?: string;
}
export declare function AppBar(props: AppBarProps): React.ReactElement;
export interface ProjectSwitcherProps {
  current?: ProjectRef;
  projects?: ProjectRef[];
  open?: boolean;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
  onSelect?: (project: ProjectRef) => void;
  /** false hides "New project". It also shows only when onCreate is given. */
  canCreate?: boolean;
  onCreate?: () => void;
  /** The menu's heading. Default "Your projects"; an admin, who sees every project, might read "All projects". */
  label?: string;
  emptyText?: string;
  className?: string;
}
export declare function ProjectSwitcher(props: ProjectSwitcherProps): React.ReactElement;
export interface UserChipProps { name: string; initials?: string; username?: string; you?: boolean; size?: 'sm' | 'md'; showName?: boolean; /** The name is a username: set it in mono. */ mono?: boolean; className?: string }
export declare function UserChip(props: UserChipProps): React.ReactElement;
export interface RoleBadgeProps { /** null renders nothing: an open server checks no role. */ role: Role | null; className?: string }
export declare function RoleBadge(props: RoleBadgeProps): React.ReactElement | null;
export interface VisibilityTagProps { visibility: Visibility; sharedWith?: number; project?: string; className?: string }
export declare function VisibilityTag(props: VisibilityTagProps): React.ReactElement;
export interface ShareDialogProps {
  subject?: string;
  project?: string;
  visibility?: Visibility;
  /** role 'owner' marks the person who started the run: shown as "Started the run", never as a role badge. */
  people: { name: string; initials?: string; username?: string; role: Role; you?: boolean }[];
  open?: boolean;
  onClose?: () => void;
  onVisibilityChange?: (visibility: Visibility) => void;
  /** Called with the typed username and chosen role, from the Add button or Enter. */
  onAdd?: (username: string, role: Role) => void;
  onRoleChange?: (person: { name: string; username?: string }, role: Role) => void;
  onRemove?: (person: { name: string; username?: string }) => void;
  /** Why the last username could not be added, shown under the field. */
  addError?: React.ReactNode;
  /** While an add is in flight: the Add button is busy and ignores clicks. */
  adding?: boolean;
  inline?: boolean;
  id?: string;
}
export declare function ShareDialog(props: ShareDialogProps): React.ReactElement;
/** A project member as the server lists it: a username and a role. name is a display name, where the consumer has one. */
export interface Member { username: string; role: Role; name?: string; initials?: string; you?: boolean; since?: string }
export interface MemberListProps {
  members: Member[];
  /** The project's name, for the words around the list. */
  project?: string;
  /** For the project's owners and server admins: the add form, role selects and remove buttons, on every row, their own included. Nothing is locked. */
  canManage?: boolean;
  /** lastOwner: the change would leave the project with no owner; self: it is your own row. The page asks first. */
  onRoleChange?: (member: Member, role: Role, about: { lastOwner: boolean; self: boolean }) => void;
  /** The page asks first, and says more when lastOwner or self. */
  onRemove?: (member: Member, about: { lastOwner: boolean; self: boolean }) => void;
  /** What the add form's username starts with. The component keeps what was typed when addError comes back. */
  defaultAddValue?: string;
  /** With canManage: the add form's Add, or Enter. */
  onAdd?: (username: string, role: Role) => void;
  addError?: React.ReactNode;
  adding?: boolean;
  /** The role every user holds without being a member (the server's everyone): editor in default. The list then says so instead of rows. */
  everyone?: Role | null;
  /** The server has no users yet: the list says how the first is made. */
  open?: boolean;
  /** Under a read-only list; false leaves it out. Default "Owners of <project> and admins of this server manage its members." */
  note?: React.ReactNode | false;
  emptyText?: React.ReactNode;
  className?: string;
}
export declare function MemberList(props: MemberListProps): React.ReactElement;
export type RulePart = string | { value: string; label?: string; choices?: boolean; word?: boolean; invalid?: boolean };
export interface RuleSentenceProps { parts: RulePart[]; effect?: React.ReactNode; onEdit?: (part: RulePart, index: number) => void; className?: string }
export declare function RuleSentence(props: RuleSentenceProps): React.ReactElement;
export interface ExportRowProps {
  title: string;
  detail?: React.ReactNode;
  state: 'queued' | 'writing' | 'ready' | 'failed' | 'expired';
  progress?: number;
  size?: string;
  onDownload?: () => void;
  onRetry?: () => void;
  className?: string;
}
export declare function ExportRow(props: ExportRowProps): React.ReactElement;

// Plugins


// Controls
export interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: 'primary' | 'secondary' | 'quiet' | 'danger';
  size?: 'sm' | 'md';
  icon?: IconName;
  /** Required when there are no children: the icon-only button's accessible name, also shown as its tooltip. */
  label?: string;
  href?: string;
  /** In flight: aria-busy, clicks ignored, a block cursor after the label. */
  busy?: boolean;
  /** Replaces the label while busy, e.g. "Saving". */
  busyLabel?: React.ReactNode;
  /** Why the button can't be used by this person. It stays focusable (aria-disabled) and shows the reason on hover and focus. */
  disabledReason?: string;
  children?: React.ReactNode;
}
export declare function Button(props: ButtonProps): React.ReactElement;
export interface FieldProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: string;
  hint?: React.ReactNode;
  error?: React.ReactNode;
  as?: 'input' | 'select' | 'textarea';
  options?: (string | { value: string; label: string })[];
  mono?: boolean;
  size?: 'sm' | 'md';
}
export declare function Field(props: FieldProps): React.ReactElement;
export interface SegmentedControlProps { options: { value: string; label: string; icon?: IconName }[]; value?: string; defaultValue?: string; onChange?: (value: string) => void; label: string; className?: string }
export declare function SegmentedControl(props: SegmentedControlProps): React.ReactElement;
export interface TabItem {
  id: string;
  /** A string may be recorded text, such as a collection's name: hidden characters show as their code points. */
  label: string;
  count?: number;
  alert?: boolean;
  /** The plugin that recorded what this tab shows; adds the plug icon, named "From <plugin>". */
  plugin?: string;
  /** The tab's content. When tabs carry panels, Tabs renders the active one as a labelled tabpanel. */
  panel?: React.ReactNode;
  /** Without panel: the id of the element this tab controls, for aria-controls. */
  controls?: string;
}
export interface TabsProps { tabs: TabItem[]; value?: string; defaultValue?: string; onChange?: (id: string) => void; label?: string; idPrefix?: string; className?: string }
export declare function Tabs(props: TabsProps): React.ReactElement;
export interface FilterBarProps { query?: string; placeholder?: string; searchId?: string; filters?: { key: string; op?: 'eq' | 'ne' | 'has' | 'lacks'; value?: string }[]; onRemove?: (filter: object, index: number) => void; onAdd?: () => void; /** With two or more filters, adds "Clear filters". */ onClear?: () => void; className?: string }
export declare function FilterBar(props: FilterBarProps): React.ReactElement;
export interface PagerProps { shown: number; hasMore: boolean; pageSize?: number; /** The order the shown items are in, said before "More exist". Default "newest first". */ order?: string; /** Plural noun, default "runs". */ noun?: string; /** Singular, default the noun without its final s. */ nounOne?: string; loading?: boolean; onMore?: () => void; className?: string }
export declare function Pager(props: PagerProps): React.ReactElement;
export interface DialogProps {
  title: string;
  /** false unmounts it and returns focus to where it was. */
  open?: boolean;
  /** Render in place, not modal: for previews and docs. */
  inline?: boolean;
  /** Called by the Close button and by Escape; the dialog stays open until open becomes false. Mark the safe choice with data-autofocus to focus it first. */
  onClose?: () => void;
  footer?: React.ReactNode;
  id?: string;
  children?: React.ReactNode;
  className?: string;
}
export declare function Dialog(props: DialogProps): React.ReactElement | null;
export interface TooltipProps {
  /** A sentence or two. Shown on hover and on keyboard focus; Escape hides it. */
  content: React.ReactNode;
  /** One focusable element. */
  children: React.ReactElement;
  /** false when content only repeats the control's accessible name (icon-only buttons), so it isn't read twice. */
  describe?: boolean;
  /** Holds it open or shut; for previews and docs. */
  open?: boolean;
  className?: string;
}
export declare function Tooltip(props: TooltipProps): React.ReactElement;
export type MenuButtonItem =
  | { separator: true }
  | {
      label: string;
      icon?: IconName;
      onSelect?: () => void;
      /** Destroys something: set in danger, and last, after a separator. */
      danger?: boolean;
      /** Why this person can't use it. The item stays, aria-disabled, with the reason under it. */
      disabledReason?: string;
    };
export interface MenuButtonProps {
  /** The accessible name, and the tooltip of an icon-only button. */
  label: string;
  items: MenuButtonItem[];
  /** Default 'more'. */
  icon?: IconName;
  /** Words beside the icon; absent, the button is icon only. */
  children?: React.ReactNode;
  variant?: 'secondary' | 'quiet';
  size?: 'sm' | 'md';
  /** Which edge the menu lines up with. Default 'end'. */
  align?: 'start' | 'end';
  /** Opens on first render; for previews and docs. */
  defaultOpen?: boolean;
  className?: string;
}
export declare function MenuButton(props: MenuButtonProps): React.ReactElement;

// Charts
/**
 * A series' colour: an outcome's own token when outcome is set, else data slot 1 to 4 in the order given
 * (slot overrides), else Other (ink-faint). Never mix outcomes and data slots in one chart.
 */
export interface SeriesBase { id: string; label: string; outcome?: Outcome; slot?: 1 | 2 | 3 | 4; other?: boolean }
export interface ChartFrameProps {
  title?: React.ReactNode;
  /** What is plotted and over what: "Last 30 runs on main". */
  caption?: React.ReactNode;
  /** A limit or an omission, under the chart. */
  note?: React.ReactNode;
  /** Heading level of the title, 2 to 6. Default 3. */
  level?: number;
  /** The same data as rows, for the Table view. */
  table?: Omit<DataTableProps<Record<string, unknown>>, 'caption'> & { caption?: string };
  /** Each entry's value at the point read, printed after its name: the legend is the readout. */
  legend?: { id: string; label: string; tone: string; hidden?: boolean; tex?: number; value?: string }[];
  /** The point read, leading the legend row: "Newest, 7f3a2c1e". */
  point?: React.ReactNode;
  legendKind?: 'line' | 'bar' | 'point';
  onToggle?: (id: string) => void;
  /** Data reloading: the drawing holds its last state at reduced opacity. */
  busy?: boolean;
  defaultView?: 'chart' | 'table';
  children?: React.ReactNode;
  className?: string;
}
export declare function ChartFrame(props: ChartFrameProps): React.ReactElement;
interface ChartCommon { title?: React.ReactNode; caption?: React.ReactNode; note?: React.ReactNode; level?: number; busy?: boolean; /** false leaves out the Table view. */ table?: false; className?: string }
export interface LineChartProps extends ChartCommon {
  /** The plot's accessible name when there is no title, as when a caption names the chart. */
  label?: string;
  /** The ordered axis: labels (runs), numbers or dates. */
  x: (string | number | Date)[];
  xType?: 'category' | 'number' | 'time';
  xLabel?: string;
  xFormat?: ValueFormat;
  xUnit?: string;
  series: (SeriesBase & { values: (number | null)[] })[];
  format?: ValueFormat;
  unit?: string;
  /** An accepted range, shaded and named; points outside it keep their dot. */
  band?: { from: number; to: number; label?: string };
  rules?: { y: number; label?: string }[];
  /** One series washed down to zero at 10%, drawn only when zero is on the axis (see zero). */
  area?: boolean;
  /** A series id kept in colour; the rest in ink-faint. */
  emphasis?: string;
  /** Dots on every point; default up to 24 points. */
  markers?: boolean;
  yMin?: number;
  yMax?: number;
  zero?: boolean;
  /** Plot height in px, axis band not included. Default 200. */
  height?: number;
}
export declare function LineChart(props: LineChartProps): React.ReactElement;
export interface BarChartProps extends ChartCommon {
  categories: string[];
  categoryLabel?: string;
  series: (SeriesBase & { values: (number | null)[] })[];
  layout?: 'vertical' | 'horizontal';
  stacked?: boolean;
  /** Bars touch but for the 2px gap, as a histogram's do. */
  fill?: boolean;
  format?: ValueFormat;
  unit?: string;
  emphasis?: string;
  hideValues?: boolean;
  /** Columns: plot height in px. Default 200. */
  height?: number;
  /** Bars: row height in px. Default 28. */
  rowHeight?: number;
}
export declare function BarChart(props: BarChartProps): React.ReactElement;
export interface HistogramProps extends Omit<ChartCommon, 'table'> {
  values: number[];
  /** About how many bins; they are round steps. */
  bins?: number;
  format?: ValueFormat;
  unit?: string;
  noun?: string;
  nounOne?: string;
  binLabel?: string;
  slot?: 1 | 2 | 3 | 4;
  zero?: boolean;
  height?: number;
}
export declare function Histogram(props: HistogramProps): React.ReactElement;
export interface ScatterChartProps extends ChartCommon {
  /** At most three coloured series, each with its own shape; more are Other. */
  series: (SeriesBase & { points: { x: number; y: number; label?: string }[] })[];
  xLabel?: string; xUnit?: string; xFormat?: ValueFormat; xMin?: number; xMax?: number; zeroX?: boolean;
  yLabel?: string; yUnit?: string; yFormat?: ValueFormat; yMin?: number; yMax?: number; zeroY?: boolean;
  pointLabel?: string;
  emphasis?: string;
  height?: number;
}
export declare function ScatterChart(props: ScatterChartProps): React.ReactElement;

// Assets
/** A file a test attached, as the server would describe it. */
export interface Asset {
  id: string;
  name: string;
  /** What the server recorded; it alone decides the viewer. */
  mediaType?: string;
  size?: number;
  sha256?: string;
  /** Where it was attached: "call phase". */
  source?: string;
  /** Its download URL, on this origin. */
  href?: string;
  /** For an image: the URL an <img> loads. Default href. */
  src?: string;
}
export interface AssetListProps { assets: Asset[]; selected?: string; onSelect?: (asset: Asset) => void; label?: string; emptyText?: React.ReactNode; className?: string }
export declare function AssetList(props: AssetListProps): React.ReactElement;
export interface AssetViewProps {
  asset: Asset;
  /** A text, JSON, CSV or TSV file's content; the page fetches it, the component never does. */
  text?: string;
  /** The text is the start of a longer file: bytes shown. The whole is asset.size; total only stands in when there is none. */
  truncated?: { shown: number; total?: number };
  loading?: boolean;
  /** Text lines shown before it says how many more there are. Default 5,000. */
  maxLines?: number;
  maxHeight?: number;
  /** Heading level of the name, 2 to 6. Default 3. */
  level?: number;
  alt?: string;
  className?: string;
}
export declare function AssetView(props: AssetViewProps): React.ReactElement;

// Feedback and layout
export interface NoticeProps { tone?: 'info' | 'warning' | 'danger'; title?: React.ReactNode; action?: React.ReactNode; children?: React.ReactNode; className?: string }
export declare function Notice(props: NoticeProps): React.ReactElement;
export interface EmptyStateProps { title: string; command?: string; action?: React.ReactNode; children?: React.ReactNode; className?: string }
export declare function EmptyState(props: EmptyStateProps): React.ReactElement;
export interface PanelProps { title?: React.ReactNode; actions?: React.ReactNode; flush?: boolean; /** Heading level of the title, 1 to 6. Default 2. */ level?: number; id?: string; children?: React.ReactNode; className?: string }
export declare function Panel(props: PanelProps): React.ReactElement;

// Tests and sections
export interface HistoryStripProps {
  /** Oldest first; null where the test did not run. */
  outcomes: (Outcome | null)[];
  changes?: (Change | null)[];
  /** The runs' labels, for the note's "since". */
  runs?: { label: string }[];
  /** false keeps the note for screen readers only. */
  note?: boolean;
  className?: string;
}
export declare function HistoryStrip(props: HistoryStripProps): React.ReactElement;
export interface TestItem {
  nodeid: string;
  /** The test's page in its project. */
  href?: string;
  /** Its section's name; null or absent when no prefix matches. */
  section?: string | null;
  latest?: { outcome: Outcome; change?: Change | null; seconds?: number | null; runLabel: string; runId?: string; runHref?: string; startedAt?: string };
  /** Its last runs, oldest first. */
  recent?: { outcomes: (Outcome | null)[]; changes?: (Change | null)[]; runs?: { label: string }[] };
}
export interface TestListProps {
  tests: TestItem[];
  now?: string | Date;
  sort?: DataTableSort | null;
  /** Default by node id, A to Z. */
  defaultSort?: DataTableSort;
  onSortChange?: (sort: DataTableSort) => void;
  maxHeight?: number | string;
  caption?: string;
  empty?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}
export declare function TestList(props: TestListProps): React.ReactElement;
export interface SectionSummaryItem { name: string; total: number; measured: number; passing: number; passPercentage: number | null }
export interface SectionListProps {
  sections: { name: string; prefix: string }[];
  /** A run's section summary: its items, its unassigned bucket, and the run it counts. Null before any run. */
  summary?: { items: SectionSummaryItem[]; unassigned: SectionSummaryItem; run?: { label: string; href?: string; note?: string } } | null;
  /** false for viewers: the controls stay, unavailable, with disabledReason. */
  canEdit?: boolean;
  /** Default "Editors and owners can change sections". */
  disabledReason?: string;
  /** The prefix comes normalised as the server stores it: trimmed, ending in one slash. */
  onAdd?: (section: { name: string; prefix: string }) => void;
  /** A new name is a rename: save the new one, then delete previousName. */
  onSave?: (section: { name: string; prefix: string }, previousName: string) => void;
  /** The page asks first. */
  onDelete?: (section: { name: string; prefix: string }) => void;
  busy?: boolean;
  /** The server's refusal, under the field it names. */
  error?: { field?: 'name' | 'prefix'; message: React.ReactNode } | null;
  adding?: boolean;
  defaultAdding?: boolean;
  onAddingChange?: (adding: boolean) => void;
  editingName?: string | null;
  onEditingChange?: (name: string | null) => void;
  /** Sections a project may hold. Default 200. */
  max?: number;
  caption?: string;
  className?: string;
}
export declare function SectionList(props: SectionListProps): React.ReactElement;

// Accounts
export type Scope = 'read' | 'record' | 'manage' | 'admin';
export interface UserItem { name: string; admin: boolean; disabled: boolean; hasPassword: boolean; createdAt: string; you?: boolean }
export interface UserListProps {
  users: UserItem[];
  onAdd?: (name: string, admin: boolean) => void;
  addError?: React.ReactNode;
  busy?: boolean;
  /** One change at a time. Your own row's demotion and disabling are unavailable, with the server's reason. Disabling asks first. */
  onUpdate?: (user: UserItem, change: { admin: boolean } | { disabled: boolean }) => void;
  onSetPassword?: (user: UserItem) => void;
  /** Shown above the table, such as "lpark has no password yet". */
  notice?: React.ReactNode;
  adding?: boolean;
  defaultAdding?: boolean;
  onAddingChange?: (adding: boolean) => void;
  className?: string;
}
export declare function UserList(props: UserListProps): React.ReactElement;
export interface TokenItem { id: number; user: string; label: string; scopes: Scope[]; createdAt: string; revokedAt: string | null; expiresAt: string | null }
export interface TokenListProps {
  tokens: TokenItem[];
  now?: string | Date;
  /** The page asks first. */
  onRevoke?: (token: TokenItem) => void;
  /** Which tokens: active (default), gone (revoked or expired) or all. */
  show?: 'active' | 'gone' | 'all';
  defaultShow?: 'active' | 'gone' | 'all';
  onShowChange?: (show: 'active' | 'gone' | 'all') => void;
  /** The bar's end, such as Make token. */
  actions?: React.ReactNode;
  /** Above the table: a NewTokenForm, or a TokenReveal. */
  children?: React.ReactNode;
  maxHeight?: number | string;
  className?: string;
}
export declare function TokenList(props: TokenListProps): React.ReactElement;
export interface NewTokenFormProps {
  users: { name: string; admin: boolean }[];
  defaultUser?: string;
  onCreate?: (token: { user: string; label: string; scopes: Scope[] }) => void;
  onCancel?: () => void;
  busy?: boolean;
  error?: React.ReactNode;
  className?: string;
}
export declare function NewTokenForm(props: NewTokenFormProps): React.ReactElement;
export interface TokenRevealProps { token: string; user: string; label?: string; scopes?: Scope[]; onDone?: () => void; className?: string }
export declare function TokenReveal(props: TokenRevealProps): React.ReactElement;
export interface NewProjectDialogProps { open?: boolean; inline?: boolean; onClose: () => void; onCreate?: (name: string) => void; busy?: boolean; error?: React.ReactNode; id?: string }
export declare function NewProjectDialog(props: NewProjectDialogProps): React.ReactElement | null;

// Recorded data and views
export type Tone = 'good' | 'bad' | 'warning' | 'neutral';
export type RecordValue = string | number | boolean | null | RecordValue[] | { [key: string]: RecordValue };
export type RecordItem = Record<string, RecordValue>;
export type FieldType = 'text' | 'number' | 'boolean' | 'json';
export interface RecordField { name: string; type?: FieldType }
/** Where a collection came from: a plugin, or the test itself (through a fixture). */
export type RecordSource = { plugin: string; version?: string } | { test: true };
export interface Collection {
  name: string;
  /** Recorded on each result, or on each run. */
  scope: 'result' | 'run';
  source: RecordSource;
  /** The fields that name the same record across runs. */
  identity?: string[];
  /** Declared fields; absent, the keys of the records in the order first seen. */
  fields?: RecordField[];
}
export interface FieldFormat { format?: 'number' | 'count' | 'seconds' | 'bytes' | 'percent'; unit?: string; label?: string }
/** A tone rule: the first that matches a record gives its tone. value may be another field of the record. */
export interface ToneRule {
  tone: Tone;
  field: string;
  op: 'is' | 'is-not' | 'lt' | 'le' | 'gt' | 'ge' | 'true' | 'false' | 'empty' | 'contains';
  value?: RecordValue | { field: string };
}
export type ViewType = 'table' | 'list' | 'keyvalue' | 'text' | 'line' | 'bar' | 'scatter';
export type Placement = 'result-tab' | 'result-chip' | 'run-tab' | 'history';
export interface ViewBindings {
  /** table */
  columns?: string[];
  /** list */
  title?: string; detail?: string; tag?: string; index?: string;
  /** keyvalue: a record's fields (all, or these), or key and value from two fields */
  fields?: string[]; key?: string; value?: string;
  /** text */
  text?: string;
  /** line and scatter: along (line) or across (scatter); y is the line's values, one field for scatter */
  x?: string; y?: string[] | string; series?: string; low?: string | number; high?: string | number; bandLabel?: string;
  /** bar */
  category?: string; values?: string[]; stacked?: boolean;
}
export interface View {
  type: ViewType;
  title?: string;
  bindings?: ViewBindings;
  format?: Record<string, FieldFormat>;
  tones?: ToneRule[];
  /** The tone of a record no rule matches. Default neutral. */
  otherwise?: Tone;
  /** tone: bad first (list and table); recorded: as recorded. */
  order?: 'tone' | 'recorded';
  placement?: Placement;
}
/** Who chose how a collection shows. */
export type ViewOrigin = { kind: 'automatic' } | { kind: 'suggested'; by: string } | { kind: 'project'; by?: string; at?: string };
export interface ToneMarkProps { tone: Tone; size?: number; label?: string; className?: string }
export declare function ToneMark(props: ToneMarkProps): React.ReactElement;
export interface RecordTableProps {
  records: RecordItem[];
  fields?: RecordField[];
  identity?: string[];
  columns?: string[];
  format?: Record<string, FieldFormat>;
  /** Aligned to records: adds a tone column. */
  tones?: Tone[];
  order?: 'tone' | 'recorded';
  caption?: string;
  dense?: boolean;
  maxHeight?: number | string;
  empty?: React.ReactNode;
  className?: string;
}
export declare function RecordTable(props: RecordTableProps): React.ReactElement;
export interface RecordListProps {
  records: RecordItem[];
  bindings: Pick<ViewBindings, 'title' | 'detail' | 'tag' | 'index'>;
  tones?: Tone[];
  format?: Record<string, FieldFormat>;
  order?: 'tone' | 'recorded';
  label?: string;
  empty?: React.ReactNode;
  className?: string;
}
export declare function RecordList(props: RecordListProps): React.ReactElement;
export interface RecordViewProps {
  view: View;
  /** One result's or run's records. */
  records?: RecordItem[];
  /** The collection across runs, oldest first: a line chart draws one line per identity. */
  runs?: { label: string; records: RecordItem[] }[];
  fields?: RecordField[];
  identity?: string[];
  title?: React.ReactNode;
  caption?: React.ReactNode;
  note?: React.ReactNode;
  /** Heading level for a chart's title. Default 3. */
  level?: number;
  height?: number;
  dense?: boolean;
  label?: string;
}
export declare function RecordView(props: RecordViewProps): React.ReactElement;
export interface RecordFrameProps {
  title: React.ReactNode;
  collection: { name: string; source?: RecordSource; count?: number };
  origin?: ViewOrigin;
  /** At the head's end, in the records' own words: "3 bad of 6". */
  status?: React.ReactNode;
  /** Editors and owners: Choose a view (automatic) or Change view. */
  onChange?: () => void;
  /** false: the button stays, unavailable, with disabledReason. */
  canChange?: boolean;
  /** Default "Editors and owners can change views". */
  disabledReason?: string;
  flush?: boolean;
  /** Heading level of the title, 1 to 6. Default 3; 2 inside a result's tab. */
  level?: number;
  children?: React.ReactNode;
  className?: string;
}
export declare function RecordFrame(props: RecordFrameProps): React.ReactElement;
export interface RecordChipProps { label?: string; value: React.ReactNode; tone?: Tone; title?: string; className?: string }
export declare function RecordChip(props: RecordChipProps): React.ReactElement;
export interface ViewEditorProps {
  collection: Collection;
  /** Real records to preview on: one result's or run's. */
  records: RecordItem[];
  /** For a history view: the collection across runs, previewed as it will show. */
  runs?: { label: string; records: RecordItem[] }[];
  acrossRuns?: boolean;
  value?: View;
  defaultValue?: View;
  onChange?: (view: View) => void;
  onSave?: (view: View) => void;
  /** With suggestedBy: "Use pytest-verify's suggestion". */
  onReset?: () => void;
  suggestedBy?: string;
  onCancel?: () => void;
  busy?: boolean;
  error?: React.ReactNode;
  /** Names the records previewed: "test_rail_under_load[max_load] in 7f3a2c1e". */
  previewNote?: React.ReactNode;
  className?: string;
}
export declare function ViewEditor(props: ViewEditorProps): React.ReactElement;
export interface ViewListItem { name: string; source: RecordSource; scope: 'result' | 'run'; view: View | null; origin: ViewOrigin; /** A plugin whose suggestion can be restored. */ suggested?: string }
export interface ViewListProps {
  items: ViewListItem[];
  canEdit?: boolean;
  disabledReason?: string;
  onEdit?: (item: ViewListItem) => void;
  onReset?: (item: ViewListItem) => void;
  onClear?: (item: ViewListItem) => void;
  className?: string;
}
export declare function ViewList(props: ViewListProps): React.ReactElement;
/** A record's tone: the first rule that matches, else otherwise, else neutral. */
export declare function recordTone(record: RecordItem, rules?: ToneRule[], otherwise?: Tone): Tone;

// Recorded text
/** Recorded text for the page: each character that prints nothing or reorders the text around it (bidi controls, zero-width characters, Hangul fillers, variation selectors, C0 and C1 controls but tab and line breaks, lone surrogates, U+FFFD) as its code point in a dl-hidden-char box; the string itself when there is none. */
export declare function visible(text: string): React.ReactNode;
/** The same for an attribute, a tooltip or an accessible name: each such character written ⟨U+202E⟩. */
export declare function visibleText(text: string): string;

declare global {
  interface Window {
    Dotline: {
      Icon: typeof Icon; Wordmark: typeof Wordmark;
      OutcomeMark: typeof OutcomeMark; OutcomeBadge: typeof OutcomeBadge; Dotline: typeof Dotline; SummaryLine: typeof SummaryLine; RunStatus: typeof RunStatus;
      HistoryGrid: typeof HistoryGrid; DurationSpark: typeof DurationSpark;
      NodeId: typeof NodeId; CommitRef: typeof CommitRef; Time: typeof Time; MetaList: typeof MetaList; DataTable: typeof DataTable; Evidence: typeof Evidence; PhaseTimeline: typeof PhaseTimeline; Command: typeof Command; RecordingGuide: typeof RecordingGuide;
      RunList: typeof RunList; PinButton: typeof PinButton; RetentionTag: typeof RetentionTag;
      AppBar: typeof AppBar; ProjectSwitcher: typeof ProjectSwitcher; UserChip: typeof UserChip; RoleBadge: typeof RoleBadge; VisibilityTag: typeof VisibilityTag;
      ShareDialog: typeof ShareDialog; MemberList: typeof MemberList; RuleSentence: typeof RuleSentence; ExportRow: typeof ExportRow;
      Button: typeof Button; Field: typeof Field; SegmentedControl: typeof SegmentedControl; Tabs: typeof Tabs; FilterBar: typeof FilterBar; Pager: typeof Pager; Dialog: typeof Dialog; Tooltip: typeof Tooltip;
      Notice: typeof Notice; EmptyState: typeof EmptyState; Panel: typeof Panel;
      ChangeBadge: typeof ChangeBadge; BaselineNote: typeof BaselineNote; ChangeQueue: typeof ChangeQueue; RerunButton: typeof RerunButton; MenuButton: typeof MenuButton;
      ChartFrame: typeof ChartFrame; LineChart: typeof LineChart; BarChart: typeof BarChart; Histogram: typeof Histogram; ScatterChart: typeof ScatterChart;
      AssetList: typeof AssetList; AssetView: typeof AssetView;
      HistoryStrip: typeof HistoryStrip; TestList: typeof TestList; SectionList: typeof SectionList;
      UserList: typeof UserList; TokenList: typeof TokenList; NewTokenForm: typeof NewTokenForm; TokenReveal: typeof TokenReveal; NewProjectDialog: typeof NewProjectDialog;
      ToneMark: typeof ToneMark; RecordTable: typeof RecordTable; RecordList: typeof RecordList; RecordView: typeof RecordView; RecordFrame: typeof RecordFrame; RecordChip: typeof RecordChip; ViewEditor: typeof ViewEditor; ViewList: typeof ViewList;
      visible: typeof visible; visibleText: typeof visibleText; recordTone: typeof recordTone;
    };
  }
}
