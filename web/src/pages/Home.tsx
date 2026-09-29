import { Navigate } from 'react-router';
import { runsHref } from '../adapt';
import { useProjects } from '../api/queries';
import { readLastProject } from '../app/lastProject';

// / opens the project whose runs were open last, while it is still one the
// person can open; otherwise default, which every server has.
export function HomePage() {
  const projects = useProjects();
  if (projects.isPending) {
    return (
      <p className="dl-caption" role="status">
        Loading…
      </p>
    );
  }
  const last = readLastProject();
  const known = projects.data?.items.some((p) => p.name === last) ?? false;
  return <Navigate to={runsHref(known && last ? last : 'default')} replace />;
}
