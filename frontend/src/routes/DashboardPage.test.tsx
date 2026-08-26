/**
 * Tests for the overview screen.
 *
 * The empty state gets the most attention here. An empty store showing zeroes
 * in the same layout as real counts would read as a clean bill of health, which
 * is the exact false reassurance this project exists to avoid.
 */

import { screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/errors';
import { ACCEPTED_RECEIPT, INVALID_RECEIPT, RUN } from '../test/fixtures';
import { renderScreen } from '../test/render';
import { DashboardPage } from './DashboardPage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

const NO_RUNS = { runs: [], total: 0, limit: 1, offset: 0 };
const NO_IMPORTS = { receipts: [], total: 0, limit: 5, offset: 0, filtered: false };

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('an empty store', () => {
  beforeEach(() => {
    client.listRuns.mockResolvedValue(NO_RUNS);
    client.listImports.mockResolvedValue(NO_IMPORTS);
  });

  it('says no run has been recorded rather than showing a run of zeroes', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/no run has been recorded yet/i)).toBeInTheDocument();
  });

  it('shows no statistics at all, so nothing reads as a completed reconciliation', async () => {
    renderScreen(<DashboardPage />);
    await screen.findByText(/no run has been recorded yet/i);

    expect(screen.queryByRole('group', { name: /run summary/i })).not.toBeInTheDocument();
  });

  it('points at importing evidence as the next step', async () => {
    renderScreen(<DashboardPage />);

    const links = await screen.findAllByRole('link', { name: /import/i });
    expect(links[0]).toHaveAttribute('href', '/imports');
  });

  it('says nothing has been imported', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/nothing has been imported/i)).toBeInTheDocument();
  });

  it('still explains the three answers, because that is what the product is', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/the three answers a line can get/i)).toBeInTheDocument();
    expect(screen.getByText(/only state that says a line is supported/i)).toBeInTheDocument();
  });

  it('offers no accuracy figure and no percentage anywhere', async () => {
    const { container } = renderScreen(<DashboardPage />);
    await screen.findByText(/no run has been recorded yet/i);

    expect(container.textContent).not.toMatch(/accuracy/i);
    expect(container.textContent).not.toMatch(/\d\s*%/);
  });
});

describe('the explanation of the three answers', () => {
  beforeEach(() => {
    client.listRuns.mockResolvedValue(NO_RUNS);
    client.listImports.mockResolvedValue(NO_IMPORTS);
    renderScreen(<DashboardPage />);
  });

  /**
   * Return the card explaining one of the three answers.
   *
   * Found by its badge rather than by the start of its text, because the badge
   * carries a decorative glyph before the word.
   */
  async function stateCard(name: string): Promise<HTMLElement> {
    const cards = await screen.findAllByRole('listitem');
    const card = cards.find((item) => within(item).queryByText(name) !== null);
    if (card === undefined) {
      throw new Error(`no card explains "${name}"`);
    }
    return card;
  }

  /**
   * Return the explanation inside one card, without its badge.
   *
   * The whole card's `textContent` runs the badge straight into the copy, as
   * "ExceptionEvery citation resolved...", which silently defeats a pattern
   * anchored with a word boundary at the start of the sentence. The wording
   * under test is the paragraph, so that is what is read.
   */
  async function stateText(name: string): Promise<string> {
    const paragraph = (await stateCard(name)).querySelector('p');
    if (paragraph === null) {
      throw new Error(`the "${name}" card has no explanation`);
    }
    return paragraph.textContent;
  }

  it('describes all three, and only three', async () => {
    const cards = await screen.findAllByRole('listitem');

    expect(cards).toHaveLength(3);
    expect(await stateCard('Resolved')).toBeInTheDocument();
    expect(await stateCard('Exception')).toBeInTheDocument();
    expect(await stateCard('Insufficient evidence')).toBeInTheDocument();
  });

  it('says a resolved line needs both its citations and its invariants', async () => {
    const card = await stateCard('Resolved');

    expect(card).toHaveTextContent(/every citation resolved/i);
    expect(card).toHaveTextContent(/every required invariant held/i);
  });

  it('does not claim that every exception has a broken rule', async () => {
    // A failed invariant means an exception. An exception does not mean a
    // failed invariant: the baseline raises one for a lifecycle state it will
    // not resolve on, with every check passing, and `line-0001` of the demo
    // corpus is exactly that. Wording that equates the two describes a failure
    // that did not happen.
    const text = await stateText('Exception');

    // The exact claim this phase removed.
    expect(text).not.toMatch(/a rule about it (does|did) not hold/i);
    // And the shapes it could come back as. Each of these asserts a failure
    // rather than naming it as one of two possibilities, which is the
    // difference that matters: "whether a check failed" is true of every
    // exception, "a check failed" is not.
    expect(text).not.toMatch(
      /\band (a|one|some) (rule|invariant|check)\b[^.]*?(does not hold|did not hold|fail)/i,
    );
    expect(text).not.toMatch(/at least one (required )?(rule|invariant|check)/i);
    expect(text).not.toMatch(/because (a|one) (rule|invariant|check)/i);
  });

  it('does not claim the evidence was present, complete, resolved or verified', async () => {
    // `derive_status` reads the exception codes before it looks at the
    // citations, so a decision citing nothing at all and carrying one ordinary
    // code is an EXCEPTION. A domain test pins that. Saying the records were
    // there describes a backing this status does not guarantee, which is the
    // second wrong thing this card has said about the same word.
    const text = await stateText('Exception');

    expect(text).not.toMatch(/records (needed|were|are)\b/i);
    expect(text).not.toMatch(/evidence (is|was|were) (there|present|complete)/i);
    expect(text).not.toMatch(
      /\b(all|every|the) (records?|citations?|evidence)\b[^.]*\b(present|there|resolved|verified|complete)\b/i,
    );
    expect(text).not.toMatch(/needed to (judge|decide)[^.]*\b(were|was) (there|present)/i);
  });

  it('says an exception is a reported non-resolution, and points at the certificate', async () => {
    // The replacement has to still describe something. A card that avoided
    // every false claim by saying nothing would pass the guards above and be
    // useless.
    const card = await stateCard('Exception');

    expect(card).toHaveTextContent(/reports a finding/i);
    expect(card).toHaveTextContent(/does not resolve this line/i);
    expect(card).toHaveTextContent(/certificate/i);
    expect(card).toHaveTextContent(/citations and the checks recorded/i);
  });

  it('keeps an exception distinct from insufficient evidence', async () => {
    // An exception is a backing carrying a reported finding or a failed
    // invariant. Insufficient evidence is a backing that does not support a
    // determinate judgement. Neither is stated in terms of what evidence was
    // present, because the status does not tell you that.
    const exception = await stateCard('Exception');
    const unknown = await stateCard('Insufficient evidence');

    expect(exception).toHaveTextContent(/reports a finding/i);
    expect(unknown).toHaveTextContent(/does not support a determinate judgement/i);
    expect(unknown).not.toHaveTextContent(/reports a finding/i);
  });

  it('does not call insufficient evidence a failure or a pass', async () => {
    const card = await stateCard('Insufficient evidence');

    expect(card).toHaveTextContent(/not a failure, and not a pass either/i);
  });
});

describe('a store with a run', () => {
  beforeEach(() => {
    client.listRuns.mockResolvedValue({ runs: [RUN], total: 1, limit: 1, offset: 0 });
    client.listImports.mockResolvedValue({
      receipts: [ACCEPTED_RECEIPT, INVALID_RECEIPT],
      total: 2,
      limit: 5,
      offset: 0,
      filtered: false,
    });
  });

  it('reports the counts the run actually recorded', async () => {
    renderScreen(<DashboardPage />);
    const summary = await screen.findByRole('group', { name: /run summary/i });

    expect(within(summary).getByText('Source facts').previousSibling).toHaveTextContent('10');
    expect(within(summary).getByText('Resolved').previousSibling).toHaveTextContent('1');
    expect(within(summary).getByText('Exceptions').previousSibling).toHaveTextContent('2');
  });

  it('shows insufficient evidence as its own number, not folded into exceptions', async () => {
    renderScreen(<DashboardPage />);
    const summary = await screen.findByRole('group', { name: /run summary/i });

    expect(within(summary).getByText('Insufficient evidence').previousSibling).toHaveTextContent(
      '0',
    );
  });

  it('names the rule versions the run used', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText(/baseline 1\.0\.0/)).toBeInTheDocument();
    expect(screen.getByText(/parser 3\.0\.0/)).toBeInTheDocument();
  });

  it('links to the audit for that run', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByRole('link', { name: /audit this run/i })).toHaveAttribute(
      'href',
      `/runs/${RUN.run_id}`,
    );
  });

  it('lists recent attempts including the refused one', async () => {
    renderScreen(<DashboardPage />);

    expect(await screen.findByText('payment_events.csv')).toBeInTheDocument();
    expect(screen.getByText('invalid_mixed_rows.csv')).toBeInTheDocument();
    expect(screen.getByText(/rejected, unreadable/i)).toBeInTheDocument();
  });
});

describe('when the backend is unreachable', () => {
  it('says so and offers a retry', async () => {
    client.listRuns.mockRejectedValue(new NetworkError());
    client.listImports.mockRejectedValue(new NetworkError());

    renderScreen(<DashboardPage />);

    expect(await screen.findAllByRole('alert')).toHaveLength(2);
    expect(screen.getAllByText(/could not be reached/i)[0]).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /try again/i })[0]).toBeInTheDocument();
  });

  it('asks again when retried', async () => {
    client.listRuns.mockRejectedValue(new NetworkError());
    client.listImports.mockRejectedValue(new NetworkError());
    renderScreen(<DashboardPage />);
    const retry = (await screen.findAllByRole('button', { name: /try again/i }))[0];

    client.listRuns.mockResolvedValue(NO_RUNS);
    retry?.click();

    await waitFor(() => {
      expect(client.listRuns).toHaveBeenCalledTimes(2);
    });
  });

  it('shows the backend message when the API refuses rather than a generic one', async () => {
    client.listRuns.mockRejectedValue(new ApiError(500, 'boom', 'the store is on fire'));
    client.listImports.mockResolvedValue(NO_IMPORTS);

    renderScreen(<DashboardPage />);

    expect(await screen.findByText('the store is on fire')).toBeInTheDocument();
  });
});
