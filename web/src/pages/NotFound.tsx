import { usePage } from '../app/page';
import { EmptyState } from '../ds';

export function NotFoundPage() {
  const heading = usePage('Not found · vantage');
  return (
    <div className="dl-stack">
      <h1 className="dl-pagehead__title app-heading" ref={heading} tabIndex={-1}>
        Not found
      </h1>
      <EmptyState
        title="Nothing at this address"
        action={
          <a className="dl-link" href="/">
            Go to the runs
          </a>
        }
      >
        This address is not a page of vantage. Runs live under /p/project/runs and /runs/run-id.
      </EmptyState>
    </div>
  );
}
