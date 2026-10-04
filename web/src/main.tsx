import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// The DOM build of the provider, which applies a navigation asked for with flushSync at once.
import { RouterProvider } from 'react-router/dom';
import './ds';
import './app/app.css';
import { createQueryClient } from './app/queryClient';
import { createAppRouter } from './app/router';

const queryClient = createQueryClient();
const router = createAppRouter(queryClient);
const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
