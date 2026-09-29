import { isApiError } from '../api/client';
import { Button, Notice } from '../ds';

// A read that failed for a reason no page explains itself: no answer, or a
// server error. Shown in place of the data, never beside a stale copy.
export function FailureNotice({
  error,
  retry,
  busy,
}: {
  error: unknown;
  retry: () => void;
  busy?: boolean;
}) {
  const unreachable = !isApiError(error) || error.status === 0;
  const action = (
    <Button size="sm" onClick={retry} busy={busy} busyLabel="Trying again">
      Try again
    </Button>
  );
  if (unreachable) {
    return (
      <Notice tone="danger" title={`Can’t reach ${window.location.host}`} action={action}>
        The server did not answer.
      </Notice>
    );
  }
  return (
    <Notice tone="danger" title={`The server answered ${error.status}`} action={action}>
      {error.detail}
    </Notice>
  );
}
