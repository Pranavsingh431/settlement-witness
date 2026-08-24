import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';

const container = document.getElementById('root');

if (!container) {
  throw new Error('Could not start the app because no element with id "root" was found.');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
