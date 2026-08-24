import './App.css';

/**
 * Workspace shell for the Settlement Witness frontend.
 *
 * This phase ships no product screens. The component exists so that the build,
 * type check, lint and test toolchain runs against real React code.
 */
export function App() {
  return (
    <main className="app">
      <h1>Settlement Witness</h1>
      <p className="app__tagline">
        An evidence-first AI finance controller for auditable payment-to-settlement reconciliation.
      </p>
      <p className="app__note">
        This is the phase 0 workspace shell. It confirms that the frontend build, test and lint
        toolchain runs. Reconciliation screens arrive in later phases.
      </p>
    </main>
  );
}
