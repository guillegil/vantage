import type { ReactNode } from 'react';
import type { NodeIdProps } from '../contract';
import { cx } from '../lib/cx';
import { visible, visibleText } from '../lib/visible';

// Path, classes, function and [parameters]; "::" inside the brackets stays in the parameters.
export function parseNodeId(v: string) {
  const br = v.indexOf('[');
  const head = br >= 0 ? v.slice(0, br) : v;
  const param = br >= 0 ? v.slice(br) : '';
  const parts = head.split('::');
  const path = parts[0] ?? '';
  const slash = path.lastIndexOf('/');
  return {
    dir: slash >= 0 ? path.slice(0, slash + 1) : '',
    file: path.slice(slash + 1),
    mids: parts.slice(1, -1),
    fn: parts.length > 1 ? (parts[parts.length - 1] ?? '') : '',
    param,
  };
}

export function NodeId(p: NodeIdProps) {
  const v = p.value || '';
  if (!v)
    return (
      <span className="dl-meta__none" title="Not recorded">
        —
      </span>
    );
  const n = parseNodeId(v);
  const head: ReactNode[] = [];
  const tail: ReactNode[] = [];
  if (n.dir) head.push(<span key="d">{p.truncate ? '…/' : visible(n.dir)}</span>);
  // Unwrapped, a long id breaks after its directory and before "::" rather than inside a name.
  if (n.dir && !p.truncate) head.push(<wbr key="dw" />);
  head.push(
    <span key="f" className={n.fn ? 'dl-nodeid__file' : 'dl-nodeid__fn'}>
      {visible(n.file)}
    </span>,
  );
  n.mids.forEach((m, i) => {
    // biome-ignore lint/suspicious/noArrayIndexKey: the classes of one node id, in order.
    head.push(<span key={`m${i}`}>{visible(`::${m}`)}</span>);
  });
  if (n.fn) {
    if (!p.truncate) tail.push(<wbr key="sw" />);
    tail.push(<span key="s">::</span>);
    tail.push(
      <span key="fn" className="dl-nodeid__fn">
        {visible(n.fn)}
      </span>,
    );
  }
  if (n.param) tail.push(<span key="pa">{visible(n.param)}</span>);
  const props = {
    className: cx(
      'dl-nodeid',
      p.truncate && 'dl-nodeid--truncate',
      p.size === 'lg' && 'dl-nodeid--lg',
      p.className,
    ),
    title: visibleText(v),
    translate: 'no' as const,
    dir: 'ltr' as const,
  };
  // Truncated, the path gives way first, so the function and its parameters stay readable.
  const body = p.truncate ? (
    <>
      <span className="dl-nodeid__head">{head}</span>
      <span className="dl-nodeid__tail">{tail}</span>
    </>
  ) : (
    <>
      {head}
      {tail}
    </>
  );
  return p.href ? (
    <a {...props} href={p.href}>
      {body}
    </a>
  ) : (
    <span {...props}>{body}</span>
  );
}
