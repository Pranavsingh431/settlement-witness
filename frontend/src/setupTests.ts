import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Vitest globals are switched off in this project, so React Testing Library
// cannot register its own automatic cleanup. Unmount every rendered tree here
// instead, otherwise one test leaves markup behind for the next one.
afterEach(() => {
  cleanup();
});
