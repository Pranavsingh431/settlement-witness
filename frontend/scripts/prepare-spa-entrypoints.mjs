import { cp, mkdir } from 'node:fs/promises';

/**
 * Vercel Services routes the original browser path into the Vite service.
 * A Vite SPA only emits `index.html`, so direct visits to its two workspace
 * routes would otherwise stop at a platform 404 before React Router starts.
 *
 * Emit static directory indexes from the built entry document. Its asset URLs
 * are root-relative, so the same generated document starts the SPA at each
 * route without duplicating a hand-maintained HTML shell.
 */
const workspaceRoutes = ['imports', 'runs'];

await Promise.all(
  workspaceRoutes.map(async (route) => {
    const directory = new URL(`../dist/${route}/`, import.meta.url);
    await mkdir(directory, { recursive: true });
    await cp(new URL('../dist/index.html', import.meta.url), new URL('index.html', directory));
  }),
);
