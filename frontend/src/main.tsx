import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { reportUncaught } from './lib/report';
import './styles.css';

reportUncaught();
try {
  sessionStorage.removeItem('reloaded-after-load-error'); // loaded fine: a later failure may reload again
} catch {
  /* storage blocked */
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  </StrictMode>,
);
