/**
 * Tests for the two proxies that carry `/v1` to the backend.
 *
 * The application only ever asks for same-origin relative paths, so if neither
 * proxy is configured the whole interface returns the app shell instead of
 * data, and every screen shows a malformed-response error. These are cheap
 * tests for a failure that is otherwise only visible by running both stacks.
 *
 * The nginx file is read as text rather than parsed. A parser for nginx syntax
 * would be a project of its own, and what needs asserting is small: that the
 * location exists, that it points at the compose service, and that it comes
 * before the single page fallback.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import config, { API_PROXY_PREFIX, API_PROXY_TARGET } from '../vite.config';

// Read from the project root rather than relative to this module, because the
// test runs in a jsdom environment where import.meta.url is not a file URL.
const nginx = readFileSync(join(process.cwd(), 'nginx.conf'), 'utf8');

describe('the Vite development proxy', () => {
  it('carries /v1 to the backend', () => {
    const proxy = config.server?.proxy ?? {};

    expect(proxy).toHaveProperty(API_PROXY_PREFIX);
    expect(proxy[API_PROXY_PREFIX]).toMatchObject({ target: API_PROXY_TARGET });
  });

  it('points at the backend development server', () => {
    expect(API_PROXY_TARGET).toBe('http://127.0.0.1:8000');
    expect(API_PROXY_PREFIX).toBe('/v1');
  });

  it('proxies only the API, so the app itself is still served by Vite', () => {
    expect(Object.keys(config.server?.proxy ?? {})).toEqual(['/v1']);
  });
});

describe('the nginx production proxy', () => {
  it('has a location for the API', () => {
    expect(nginx).toMatch(/location\s+\/v1\/\s*\{/);
  });

  it('sends it to the backend service on the Compose network', () => {
    expect(nginx).toMatch(/set \$api_backend http:\/\/backend:8000;/);
    expect(nginx).toMatch(/proxy_pass \$api_backend;/);
  });

  it('resolves the upstream per request rather than at startup', () => {
    // With the host written literally, nginx resolves it while starting and
    // refuses to start when it cannot, so the frontend image would not run
    // unless a host called `backend` already existed. Passing it through a
    // variable defers resolution, so the image starts either way and a request
    // made with no backend behind it fails as a bad gateway.
    expect(nginx).toMatch(/resolver 127\.0\.0\.11/);
    expect(nginx).not.toMatch(/proxy_pass\s+http:\/\/backend/);
  });

  it('names no host the browser would have to know', () => {
    expect(nginx).not.toMatch(/http:\/\/localhost/);
    expect(nginx).not.toMatch(/set \$\w+ http:\/\/127\.0\.0\.1/);
  });

  it('declares the API before the single page fallback', () => {
    // Otherwise an unknown /v1 path would return the app shell with status 200,
    // and the client would report a malformed response instead of the
    // backend's own 404.
    expect(nginx.indexOf('location /v1/')).toBeLessThan(nginx.indexOf('try_files'));
  });

  it('keeps the single page fallback for the app itself', () => {
    expect(nginx).toMatch(/try_files \$uri \$uri\/ \/index\.html;/);
  });

  it('exposes the backend health endpoint, so the container can be checked end to end', () => {
    // The backend serves health at /health, so the path is rewritten. A URI
    // cannot ride on a variable upstream the way it can on a literal one.
    expect(nginx).toMatch(/location\s+=\s+\/v1\/health\s*\{/);
    expect(nginx).toMatch(/rewrite \^ \/health break;/);
    expect(nginx).toMatch(/proxy_pass \$health_backend;/);
  });

  it('raises the body limit above nginx default, so an upload is bounded by the backend', () => {
    // The backend counts the request body before anything parses it. If nginx
    // refused first with its 1 MB default, a document the backend would accept
    // would be rejected here instead, with a different error and no receipt.
    expect(nginx).toMatch(/client_max_body_size\s+64m;/);
  });

  it('still binds an unprivileged port', () => {
    expect(nginx).toMatch(/listen 8080;/);
    expect(nginx).not.toMatch(/listen 80;/);
  });
});
