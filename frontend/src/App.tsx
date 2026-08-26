import { NavLink, Route, Routes } from 'react-router-dom';

import { DashboardPage } from './routes/DashboardPage';
import { ImportsPage } from './routes/ImportsPage';
import { RunAuditPage } from './routes/RunAuditPage';
import { RunsPage } from './routes/RunsPage';

const LINKS = [
  { to: '/', label: 'Overview' },
  { to: '/imports', label: 'Import evidence' },
  { to: '/runs', label: 'Runs' },
] as const;

/**
 * The application shell: one masthead, one nav, and the current screen.
 *
 * The skip link and the `main` landmark are here rather than in each screen, so
 * keyboard and screen reader users get past the navigation once instead of on
 * every route.
 */
export function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="masthead">
        <div className="masthead__inner">
          <span className="masthead__name">Settlement Witness</span>
          <span className="masthead__claim">Evidence-first settlement reconciliation</span>
          <nav className="masthead__nav" aria-label="Sections">
            {LINKS.map((link) => (
              <NavLink key={link.to} className="masthead__link" to={link.to} end={link.to === '/'}>
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="page" id="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/imports" element={<ImportsPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunAuditPage />} />
          <Route
            path="*"
            element={
              <div className="empty">
                <p className="empty__title">No such page</p>
                <p>
                  The address you asked for is not part of this application. Use the navigation
                  above.
                </p>
              </div>
            }
          />
        </Routes>
      </main>
    </>
  );
}
