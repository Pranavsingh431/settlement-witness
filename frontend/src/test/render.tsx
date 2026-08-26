/**
 * Rendering a screen the way the application renders it.
 *
 * Every screen sits inside a router and reads from the API client, so a test
 * that renders one without both is testing something the application never
 * does. This puts the router back and leaves the client to each test to mock,
 * because what the backend answered is the thing under test.
 */

import { render } from '@testing-library/react';
import type { RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

export function renderScreen(element: ReactElement, path = '/'): RenderResult {
  return render(<MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>);
}

/** Render a screen that reads a route parameter, at a real address. */
export function renderRoute(pattern: string, element: ReactElement, at: string): RenderResult {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Routes>
        <Route path={pattern} element={element} />
      </Routes>
    </MemoryRouter>,
  );
}
