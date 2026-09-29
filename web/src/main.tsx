import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
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
