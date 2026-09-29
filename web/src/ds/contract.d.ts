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
  | 'square' | 'trash' | 'plus' | 'external' | 'sliders' | 'log-out' | 'archive' | 'plug' | 'dice' | 'target' | 'flask' | 'terminal'
  | 'info' | 'warning' | 'more' | 'menu' | 'play';

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
  className?: string;
}
export declare function RunStatus(props: RunStatusProps): React.ReactElement;

// History
export interface HistoryGridProps {
  /** Columns, oldest first. label is the run's label, as lists print it (the first 8 characters of its id); detail, such as the branch and commit, joins it in the readout. */
  runs: { id: string; label: string; detail?: string }[];
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
export interface DataTableColumn<Row> { key: string; label: React.ReactNode; align?: 'start' | 'num' | 'end'; width?: number | string; render?: (row: Row) => React.ReactNode }
export interface DataTableProps<Row> {
  columns: DataTableColumn<Row>[];
  rows: Row[];
  rowKey?: keyof Row;
  selected?: unknown;
  dense?: boolean;
  /** Read by screen readers, and names the scroll region when the table overflows. */
  caption?: string;
  /** Shown in a single row when there are no rows. Default "Nothing to show." */
  empty?: React.ReactNode;
  className?: string;
}
export declare function DataTable<Row>(props: DataTableProps<Row>): React.ReactElement;
export interface EvidenceProps {
  title?: string;
  text: string;
  /** 'traceback' marks pytest's E, > and path.py:N: lines. */
  kind?: 'traceback' | 'output' | 'log';
  /** true, or a sentence saying what was kept. */
  truncated?: boolean | string;
  notice?: React.ReactNode;
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
  outcome: Outcome;
  seconds?: number | null;
  change?: Change | null;
  was?: Outcome | null;
  streak?: { runs: number; since?: string };
  href?: string;
}
/** A test the baseline had and this run lacks: removed, or not reached when the run stopped early. */
export interface MissingResult { nodeid: string; was?: Outcome | null }
export interface ChangeQueueDetailContext {
  /** The selected test's rerun command, null for a test this run lacks. */
  command: string | null;
  baseline: string | null;
  /** Position in what the queue shows, from 0; -1 when the selected test is filtered out. */
  index: number;
  count: number;
}
export interface ChangeQueueProps {
  /** This run's results in collection order. */
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
  /** Rows per page in the whole-run view. Default 200. */
  allPageSize?: number;
  defaultView?: 'changes' | 'all';
  label?: string;
  className?: string;
}
export declare function ChangeQueue(props: ChangeQueueProps): React.ReactElement;
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
export interface AccountMenuItem { label: string; icon?: IconName; onSelect: () => void }
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
export interface UserChipProps { name: string; initials?: string; username?: string; you?: boolean; size?: 'sm' | 'md'; showName?: boolean; className?: string }
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
export interface Member { name: string; initials?: string; username?: string; role: Role; you?: boolean; since?: string }
export interface MemberListProps {
  members: Member[];
  /** For the project's owners and server admins. The last owner's role and removal stay locked. */
  canManage?: boolean;
  onRoleChange?: (member: Member, role: Role) => void;
  onRemove?: (member: Member) => void;
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
export interface PluginPanelProps { title: string; plugin: string; version?: string; status?: React.ReactNode; flush?: boolean; /** Heading level, 1 to 6. Default 3. */ level?: number; children?: React.ReactNode; className?: string }
export declare function PluginPanel(props: PluginPanelProps): React.ReactElement;
export interface Check { index?: number; name: string; passed: boolean; detail?: string; branch?: string; children?: Check[] }
export interface CheckListProps { checks: Check[]; summary?: boolean; /** Shown when there are no checks. Default "No checks recorded." */ emptyText?: React.ReactNode; className?: string }
export declare function CheckList(props: CheckListProps): React.ReactElement;
export interface Vector { index: number; kind: 'random' | 'directed' | 'test'; name?: string; args: Record<string, string | number | boolean | null>; outcome: Outcome; seconds?: number }
export interface VectorTableProps { strategy: string; nodeid: string; seed?: string | number; vectors: Vector[]; dense?: boolean; className?: string }
export declare function VectorTable(props: VectorTableProps): React.ReactElement;

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
  label: string;
  count?: number;
  alert?: boolean;
  /** The plugin that contributes this tab; adds the plug icon. */
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
export interface PagerProps { shown: number; hasMore: boolean; pageSize?: number; /** Plural noun, default "runs". */ noun?: string; /** Singular, default the noun without its final s. */ nounOne?: string; loading?: boolean; onMore?: () => void; className?: string }
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

// Feedback and layout
export interface NoticeProps { tone?: 'info' | 'warning' | 'danger'; title?: React.ReactNode; action?: React.ReactNode; children?: React.ReactNode; className?: string }
export declare function Notice(props: NoticeProps): React.ReactElement;
export interface EmptyStateProps { title: string; command?: string; action?: React.ReactNode; children?: React.ReactNode; className?: string }
export declare function EmptyState(props: EmptyStateProps): React.ReactElement;
export interface PanelProps { title?: React.ReactNode; actions?: React.ReactNode; flush?: boolean; /** Heading level of the title, 1 to 6. Default 2. */ level?: number; id?: string; children?: React.ReactNode; className?: string }
export declare function Panel(props: PanelProps): React.ReactElement;

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
      PluginPanel: typeof PluginPanel; CheckList: typeof CheckList; VectorTable: typeof VectorTable;
      Button: typeof Button; Field: typeof Field; SegmentedControl: typeof SegmentedControl; Tabs: typeof Tabs; FilterBar: typeof FilterBar; Pager: typeof Pager; Dialog: typeof Dialog; Tooltip: typeof Tooltip;
      Notice: typeof Notice; EmptyState: typeof EmptyState; Panel: typeof Panel;
      ChangeBadge: typeof ChangeBadge; ChangeQueue: typeof ChangeQueue; RerunButton: typeof RerunButton; MenuButton: typeof MenuButton;
    };
  }
}
