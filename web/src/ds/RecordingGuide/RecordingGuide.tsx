import { createElement, type ReactNode } from 'react';
import { Command } from '../Command/Command';
import type { RecordingGuideProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { useCopy } from '../lib/copy';
import { cx } from '../lib/cx';
import { Notice } from '../Notice/Notice';

// A multi-line text a person copies whole, such as an ini section.
function Snippet(p: { text: string; label?: string }) {
  const [copied, copy] = useCopy(p.text);
  return (
    <div className="dl-snippet" data-copy-scope="">
      <pre
        className="dl-snippet__text"
        dir="ltr"
        data-copy-text=""
        tabIndex={0}
        aria-label={p.label}
      >
        {p.text}
      </pre>
      <button
        type="button"
        className="dl-cmd__copy dl-snippet__copy"
        onClick={copy}
        aria-label={`Copy ${p.label || 'text'}`}
      >
        <Icon name={copied ? 'check' : 'copy'} size={14} />
        {copied ? 'Copied' : 'Copy'}
      </button>
      <span className="dl-sr" aria-live="polite">
        {copied ? 'Copied' : ''}
      </span>
    </div>
  );
}

const PLUGIN_DEFAULT_SERVER = 'http://127.0.0.1:8765';

// How a person gets their first run into a project, from this server's own facts: its address, whether it has
// accounts, and whether their role can record. The same guide fills the empty run list and project settings.
export function RecordingGuide(p: RecordingGuideProps) {
  const server = p.server || PLUGIN_DEFAULT_SERVER;
  const project = p.project || 'default';
  const parts = ['pytest', '--vantage'];
  // The plugin already reports to its default address and a run with no project goes to default,
  // so neither is spelled out when it would change nothing.
  if (server !== PLUGIN_DEFAULT_SERVER) parts.push(`--vantage-server ${server}`);
  if (project !== 'default') parts.push(`--vantage-project ${project}`);
  const ini = `[pytest]\nvantage_server = ${server}${project !== 'default' ? `\nvantage_project = ${project}` : ''}`;
  const level = Math.min(6, Math.max(2, p.level || 3));
  const title =
    p.title === false
      ? null
      : createElement(
          `h${level}`,
          { className: 'dl-guide__title' },
          p.title || `Record a run into ${project}`,
        );
  if (p.canRecord === false) {
    return (
      <div className={cx('dl-guide', p.className)}>
        {title}
        <Notice title="Your role here can read runs, not record them">
          {p.cannotRecord ||
            `Viewers read ${project}’s runs. An owner of ${project} can make you an editor.`}
        </Notice>
      </div>
    );
  }
  const steps: ReactNode[] = [];
  if (!p.open) {
    steps.push(
      <li key="token" className="dl-step">
        <span className="dl-step__n" aria-hidden="true">
          {String(steps.length + 1)}
        </span>
        <div className="dl-step__body">
          <span className="dl-step__title">Get a token that can record</span>
          <p className="dl-step__text">
            An admin of this server makes one, and it is shown once. The plugin reads it from{' '}
            <code>VANTAGE_TOKEN</code> and from nowhere else, so set it in the environment the suite
            runs in.
          </p>
        </div>
      </li>,
    );
  }
  steps.push(
    <li key="run" className="dl-step">
      <span className="dl-step__n" aria-hidden="true">
        {String(steps.length + 1)}
      </span>
      <div className="dl-step__body">
        <span className="dl-step__title">
          Run your suite with <code>--vantage</code>
        </span>
        <Command text={parts.join(' ')} />
        <p className="dl-step__text">
          {`The run appears in ${project} as soon as it starts, and fills in while it runs.${
            p.open ? ' This server has no accounts, so no token is needed.' : ''
          }`}
        </p>
      </div>
    </li>,
  );
  return (
    <div className={cx('dl-guide', p.className)}>
      {title}
      <ol className="dl-steps">{steps}</ol>
      {/* When the command is already just `pytest --vantage`, the ini form would only restate the plugin's defaults. */}
      {parts.length === 2 ? null : (
        <div className="dl-guide__alt">
          <p className="dl-step__text">
            Or set the address and project once in <code>pytest.ini</code> (in{' '}
            <code>pyproject.toml</code>, under <code>[tool.pytest.ini_options]</code>). Then{' '}
            <code>pytest --vantage</code> is enough.
          </p>
          <Snippet text={ini} label="pytest.ini settings" />
        </div>
      )}
      <details className="dl-disclosure">
        <summary>
          <Icon name="chevron-right" size={14} />
          Run not showing up?
        </summary>
        <ul className="dl-disclosure__body">
          <li>
            Type <code>--vantage</code> on the command line itself. From <code>addopts</code>,{' '}
            <code>PYTEST_ADDOPTS</code> or an <code>@file</code> it is ignored, with a warning.
          </li>
          <li>
            Look for a <code>VantageWarning</code> at the end of pytest’s output: it says why a run
            was not recorded. The suite’s own result never changes.
          </li>
          {p.open ? null : (
            <li>
              {`A run the server refused, for its token or because it does not know ${project}, stays in the plugin’s outbox. Fix the cause, then send it with `}
              <code>vantage push</code> from the <code>vantage</code> package.
            </li>
          )}
        </ul>
      </details>
    </div>
  );
}
