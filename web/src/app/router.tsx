import type { QueryClient } from '@tanstack/react-query';
import { createBrowserRouter, Navigate, type RouteObject, useParams } from 'react-router';
import { runsHref } from '../adapt';
import { HistoryPage } from '../pages/History';
import { HomePage } from '../pages/Home';
import { NotFoundPage } from '../pages/NotFound';
import { ResultPage } from '../pages/Result';
import { RunPage } from '../pages/Run';
import { RunsPage } from '../pages/Runs';
import { SignInPage } from '../pages/SignIn';
import { Root } from './Root';

function ProjectHome() {
  const { project = 'default' } = useParams();
  return <Navigate to={runsHref(project)} replace />;
}

// Node ids travel in the query string, as they do in the API.
export const routes: RouteObject[] = [
  { path: '/sign-in', element: <SignInPage /> },
  {
    path: '/',
    element: <Root />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'p/:project', element: <ProjectHome /> },
      { path: 'p/:project/runs', element: <RunsPage /> },
      { path: 'p/:project/tests/history', element: <HistoryPage /> },
      { path: 'runs/:runId', element: <RunPage /> },
      { path: 'runs/:runId/result', element: <ResultPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export function createAppRouter(_queryClient: QueryClient) {
  return createBrowserRouter(routes);
}
