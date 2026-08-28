import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const stylesheet = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');

describe('responsive grid containment', () => {
  it('allows a grid panel to shrink so a wide table scrolls inside it', () => {
    expect(stylesheet).toMatch(/\.grid\s*>\s*\*\s*\{\s*min-width:\s*0;/);
  });

  it('lets opaque certificate identities wrap rather than widening the page', () => {
    expect(stylesheet).toMatch(
      /\.check__title,\s*\.check__detail\s*\{\s*\/\*[^*]*\*\/\s*min-width:\s*0;\s*overflow-wrap:\s*anywhere;/,
    );
  });
});
