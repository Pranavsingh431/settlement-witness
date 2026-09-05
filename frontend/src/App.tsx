import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom';

import { DashboardPage } from './routes/DashboardPage';
import { ImportsPage } from './routes/ImportsPage';
import { RunAuditPage } from './routes/RunAuditPage';
import { ReviewQueuePage } from './routes/ReviewQueuePage';
import { RunsPage } from './routes/RunsPage';
import { DeskPage } from './routes/DeskPage';
import { CashPage } from './routes/CashPage';
import { Icon } from './components/Icon';

const LINKS = [
  { to: '/imports', label: 'Data sources', icon: 'file' },
  { to: '/cash', label: 'Bank credits', icon: 'bank' },
  { to: '/runs', label: 'Audit history', icon: 'clock' },
  { to: '/benchmark', label: 'Benchmark', icon: 'chart' },
] as const;

/**
 * The application shell: one masthead, one nav, and the current screen.
 *
 * The skip link and the `main` landmark are here rather than in each screen, so
 * keyboard and screen reader users get past the navigation once instead of on
 * every route.
 */
export function App() {
  const location = useLocation();
  const issues = new URLSearchParams(location.search).get('view') === 'issues';
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="app-sidebar">
        <Link className="app-brand" to="/">
          <span className="brand-mark">w</span>
          <span>
            Settlement Witness<small>FINANCE WORKSPACE</small>
          </span>
        </Link>
        <div className="workspace-switch">
          <span className="workspace-avatar">SW</span>
          <span>
            Sample workspace<small>Shared synthetic data</small>
          </span>
        </div>
        <p className="nav-caption">WORKSPACE</p>
        <nav className="app-navigation" aria-label="Sections">
          <Link to="/" aria-current={location.pathname === '/' && !issues ? 'page' : undefined}>
            <Icon name="home" />
            Overview
          </Link>
          <Link
            to="/?view=issues"
            aria-current={location.pathname === '/' && issues ? 'page' : undefined}
          >
            <Icon name="inbox" />
            Attention inbox
          </Link>
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to}>
              <Icon name={link.icon} />
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <Icon name="shield" />
          <strong>A clear trail, for every rupee.</strong>
          <p>Follow a result back to the records that support it.</p>
          <Link to="/benchmark">
            Explore the checks <Icon name="arrow" size={15} />
          </Link>
        </div>
        <div className="sidebar-edition">
          Settlement Witness <span>01 / Finance</span>
        </div>
      </aside>
      <header className="app-topbar" aria-label="Workspace">
        <span>
          Workspace <span className="topbar-divider">/</span> <strong>Finance operations</strong>
        </span>
        <div>
          <span className="environment-label">
            <i /> Synthetic workspace
          </span>
          <details className="workspace-help">
            <summary aria-label="Workspace help">
              <Icon name="help" />
            </summary>
            <div>
              <strong>Start with a sample business</strong>
              <p>
                Load the sample from Overview, open an issue, then download its evidence request.
                Add returned records from Data sources and reconcile again.
              </p>
              <p>
                This shared workspace is for synthetic records. No bank connection or money movement
                is performed.
              </p>
              <Link to="/benchmark">Inspect benchmark results →</Link>
            </div>
          </details>
        </div>
      </header>
      <main className="page" id="main">
        <Routes>
          <Route path="/" element={<DeskPage />} />
          <Route path="/benchmark" element={<DashboardPage />} />
          <Route path="/cash" element={<CashPage />} />
          <Route path="/imports" element={<ImportsPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunAuditPage />} />
          <Route path="/runs/:runId/review" element={<ReviewQueuePage />} />
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
