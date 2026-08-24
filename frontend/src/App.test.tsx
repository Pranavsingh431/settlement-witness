import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { App } from './App';

describe('App', () => {
  it('shows the project name as the page heading', () => {
    render(<App />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Settlement Witness');
  });

  it('states plainly that this is the phase 0 shell', () => {
    render(<App />);

    expect(screen.getByText(/phase 0 workspace shell/i)).toBeInTheDocument();
  });
});
