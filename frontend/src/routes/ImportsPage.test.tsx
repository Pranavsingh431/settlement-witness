/**
 * Tests for the import screen.
 *
 * The four outcomes are the point. A judge has to be able to tell, without
 * reading a count, whether a document was taken, was a replay, was unreadable
 * or contradicted something already stored, and whether any facts were written.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/errors';
import {
  ACCEPTED_RECEIPT,
  CONFLICT_RECEIPT,
  DUPLICATE_RECEIPT,
  INVALID_RECEIPT,
} from '../test/fixtures';
import { renderScreen } from '../test/render';
import { ImportsPage } from './ImportsPage';

vi.mock('../api/client');
const client = vi.mocked(await import('../api/client'));

const EMPTY = { receipts: [], total: 0, limit: 10, offset: 0, filtered: false };

/** The bytes of a real document, used to prove none of them are rendered. */
const CSV_TEXT =
  'provider_event_id,event_id,payment_id,merchant_id,event_type,amount_minor,currency,occurred_at\n' +
  'pe-0001,evt-0001,pay-0001,merch-01,CAPTURE,1000000,INR,2026-08-20T09:15:00+05:30\n';

/** A promise that never settles, for testing an in-flight request. */
function never<T>(): Promise<T> {
  return new Promise<T>(noop);
}

function noop(): void {
  // Deliberately does nothing.
}

function csvFile(name = 'payment_events.csv'): File {
  return new File([CSV_TEXT], name, { type: 'text/csv' });
}

async function uploadFile(file = csvFile()): Promise<void> {
  const input = screen.getByLabelText('CSV document');
  await userEvent.upload(input, file);
  await userEvent.click(screen.getByRole('button', { name: /import document/i }));
}

beforeEach(() => {
  vi.resetAllMocks();
  client.listImports.mockResolvedValue(EMPTY);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the upload form', () => {
  it('will not submit until a file is chosen', async () => {
    renderScreen(<ImportsPage />);

    expect(await screen.findByRole('button', { name: /import document/i })).toBeDisabled();
  });

  it('offers only the record types the parser has a schema for', async () => {
    renderScreen(<ImportsPage />);
    const select = await screen.findByLabelText(/declared record type/i);

    const options = within(select)
      .getAllByRole('option')
      .map((one) => one.textContent);
    expect(options).toEqual(['PAYMENT_EVENT', 'SETTLEMENT_LINE', 'PAYOUT']);
    expect(options).not.toContain('BANK_TRANSACTION');
  });

  it('says plainly that neither declaration is inferred from the file', async () => {
    renderScreen(<ImportsPage />);

    expect(await screen.findByText(/never inferred from the headers/i)).toBeInTheDocument();
    expect(
      screen.getByText(/never inferred: a file read as the wrong system/i),
    ).toBeInTheDocument();
  });

  it('names the three documents this demo expects', async () => {
    renderScreen(<ImportsPage />);

    expect(await screen.findByText('payment_events.csv')).toBeInTheDocument();
    expect(screen.getByText('settlement_lines.csv')).toBeInTheDocument();
    expect(screen.getByText('payouts.csv')).toBeInTheDocument();
  });

  it('sends what the person declared, not anything read from the file', async () => {
    client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await userEvent.selectOptions(
      screen.getByLabelText(/declared source system/i),
      'BANK_STATEMENT',
    );
    await userEvent.selectOptions(screen.getByLabelText(/declared record type/i), 'PAYOUT');
    await uploadFile(csvFile('anything.csv'));

    expect(client.importDocument).toHaveBeenCalledWith(
      expect.any(File),
      'BANK_STATEMENT',
      'PAYOUT',
    );
  });

  it('can be completed with the keyboard alone', async () => {
    client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
    renderScreen(<ImportsPage />);
    const input = await screen.findByLabelText('CSV document');
    await userEvent.upload(input, csvFile());

    // Tab from the file input, through both selects, to the submit button.
    input.focus();
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole('button', { name: /import document/i })).toHaveFocus();

    await userEvent.keyboard('{Enter}');

    await waitFor(() => {
      expect(client.importDocument).toHaveBeenCalledTimes(1);
    });
  });

  it('refuses a second submit while one is in flight', async () => {
    let release: (receipt: typeof ACCEPTED_RECEIPT) => void = noop;
    client.importDocument.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });
    await uploadFile();

    const button = screen.getByRole('button', { name: /importing/i });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(client.importDocument).toHaveBeenCalledTimes(1);

    release(ACCEPTED_RECEIPT);
    await screen.findByText(/facts were written/i);
  });

  it('announces that an import is running', async () => {
    client.importDocument.mockReturnValue(never());
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    expect(screen.getByText(/waiting for the server to record a receipt/i)).toBeInTheDocument();
  });
});

describe('the receipt that comes back', () => {
  it('reports an accepted document as accepted, with what was stored', async () => {
    client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    expect(await screen.findByText(/every row was new and all of them were stored/i)).toBeVisible();
    expect(screen.getByText(/^Facts were written\.$/)).toBeInTheDocument();
  });

  it('reports a replay as a duplicate that changed nothing', async () => {
    client.importDocument.mockResolvedValue(DUPLICATE_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    expect(await screen.findByText(/nothing changed/i)).toBeInTheDocument();
    expect(screen.getByText(/correct result, not an error/i)).toBeInTheDocument();
    expect(screen.getByText(/^No facts were written\.$/)).toBeInTheDocument();
  });

  it('reports an unreadable document and says the receipt was kept', async () => {
    client.importDocument.mockResolvedValue(INVALID_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    expect(await screen.findByText(/rejected, unreadable/i)).toBeInTheDocument();
    expect(screen.getByText(/no facts were written.*receipt below is still stored/i)).toBeVisible();
    expect(screen.getByText('2 row(s) could not be read')).toBeInTheDocument();
  });

  it('reports a conflicting document distinctly from an unreadable one', async () => {
    client.importDocument.mockResolvedValue(CONFLICT_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    expect(await screen.findByText(/rejected, conflicting/i)).toBeInTheDocument();
    expect(screen.getByText(/contradicts a fact already stored/i)).toBeInTheDocument();
    expect(screen.queryByText(/rejected, unreadable/i)).not.toBeInTheDocument();
  });

  it('shows what happened to each row, with the code and the rule', async () => {
    client.importDocument.mockResolvedValue(INVALID_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();
    await screen.findByText(/rejected, unreadable/i);

    expect(screen.getByText('INVALID_ENUM')).toBeInTheDocument();
    expect(screen.getByText('amount_minor is required and was empty')).toBeInTheDocument();
    expect(screen.getByText('Readable, not stored')).toBeInTheDocument();
  });

  it('shows every count from the receipt', async () => {
    client.importDocument.mockResolvedValue(INVALID_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();
    const summary = await screen.findByRole('group', { name: /import summary/i });

    expect(within(summary).getByText('Rows read').previousSibling).toHaveTextContent('3');
    expect(within(summary).getByText('Unreadable').previousSibling).toHaveTextContent('2');
    expect(within(summary).getByText('Not applied').previousSibling).toHaveTextContent('1');
  });

  it('renders no line of the uploaded document anywhere', async () => {
    client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
    const { container } = renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();
    await screen.findByText(/every row was new/i);

    expect(container.textContent).not.toContain('pe-0001,evt-0001');
    expect(container.textContent).not.toContain('provider_event_id,event_id');
    expect(container.textContent).not.toContain('1000000');
  });
});

describe('when the upload is refused before the service sees it', () => {
  it('shows the size limit message and says no receipt was written', async () => {
    client.importDocument.mockRejectedValue(
      new ApiError(
        413,
        'document_too_large',
        'the uploaded document is larger than the 8388608 byte limit; no import was processed and no receipt was written',
      ),
    );
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/larger than the 8388608 byte limit/);
    expect(alert).toHaveTextContent(/no receipt was written for this attempt/i);
  });

  it('shows an unreachable backend as its own problem', async () => {
    client.importDocument.mockRejectedValue(new NetworkError());
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be reached/i);
  });

  it('lets the person try again afterwards', async () => {
    client.importDocument.mockRejectedValueOnce(new NetworkError());
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });
    await uploadFile();
    await screen.findByRole('alert');

    client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
    await userEvent.click(screen.getByRole('button', { name: /import document/i }));

    expect(await screen.findByText(/every row was new/i)).toBeInTheDocument();
  });
});

describe('the import history', () => {
  it('says when there is nothing yet', async () => {
    renderScreen(<ImportsPage />);

    expect(await screen.findByText(/no imports yet/i)).toBeInTheDocument();
  });

  it('lists every attempt with its outcome', async () => {
    client.listImports.mockResolvedValue({
      receipts: [ACCEPTED_RECEIPT, INVALID_RECEIPT],
      total: 2,
      limit: 10,
      offset: 0,
      filtered: false,
    });
    renderScreen(<ImportsPage />);

    expect(await screen.findByText('2 attempt(s) in total.')).toBeInTheDocument();
    expect(screen.getByText('invalid_mixed_rows.csv')).toBeInTheDocument();
  });

  it('passes a chosen filter to the API', async () => {
    renderScreen(<ImportsPage />);
    await screen.findByLabelText('Outcome');

    await userEvent.selectOptions(screen.getByLabelText('Outcome'), 'REJECTED_INVALID');

    await waitFor(() => {
      expect(client.listImports).toHaveBeenLastCalledWith(
        expect.objectContaining({ outcome: 'REJECTED_INVALID' }),
      );
    });
  });

  it('says when a view is filtered, so the total is not read as the whole history', async () => {
    client.listImports.mockResolvedValue({
      receipts: [INVALID_RECEIPT],
      total: 1,
      limit: 10,
      offset: 0,
      filtered: true,
    });
    renderScreen(<ImportsPage />);

    expect(
      await screen.findByText(/this is a filtered view, not the whole history/i),
    ).toBeInTheDocument();
  });

  it('says when a filter matches nothing, rather than looking empty', async () => {
    client.listImports.mockResolvedValue({ ...EMPTY, filtered: true });
    renderScreen(<ImportsPage />);

    expect(await screen.findByText(/no attempt matches these filters/i)).toBeInTheDocument();
  });

  it('pages through the history', async () => {
    client.listImports.mockResolvedValue({
      receipts: [ACCEPTED_RECEIPT],
      total: 25,
      limit: 10,
      offset: 0,
      filtered: false,
    });
    renderScreen(<ImportsPage />);
    await screen.findByText('Page 1 of 3');

    expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /next/i }));

    await waitFor(() => {
      expect(client.listImports).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 10 }));
    });
  });

  it('reloads the history after a successful import', async () => {
    client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });
    const before = client.listImports.mock.calls.length;

    await uploadFile();
    await screen.findByText(/every row was new/i);

    await waitFor(() => {
      expect(client.listImports.mock.calls.length).toBeGreaterThan(before);
    });
  });
});

describe('dropping a file onto the form', () => {
  function dropzone(): HTMLElement {
    const zone = document.querySelector('.dropzone');
    if (zone === null) {
      throw new Error('the form has no drop zone');
    }
    return zone as HTMLElement;
  }

  // jsdom implements no DataTransfer, so the drop event carries the same shape
  // the handler reads: a files list.
  function transfer(...files: File[]): { files: File[] } {
    return { files };
  }

  it('selects the dropped file', async () => {
    client.importDocument.mockResolvedValue(ACCEPTED_RECEIPT);
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    fireEvent.drop(dropzone(), { dataTransfer: transfer(csvFile('dropped.csv')) });

    expect(await screen.findByText('dropped.csv')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /import document/i })).toBeEnabled();
  });

  it('shows the zone is active while a file is over it', async () => {
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    fireEvent.dragOver(dropzone());
    expect(dropzone().className).toContain('is-over');

    fireEvent.dragLeave(dropzone());
    expect(dropzone().className).not.toContain('is-over');
  });

  it('ignores a drop that carries no file', async () => {
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    fireEvent.drop(dropzone(), { dataTransfer: transfer() });

    expect(screen.getByRole('button', { name: /import document/i })).toBeDisabled();
  });
});

describe('the other history filters', () => {
  it('passes a source system filter to the API', async () => {
    renderScreen(<ImportsPage />);
    await screen.findByLabelText('Source system');

    await userEvent.selectOptions(screen.getByLabelText('Source system'), 'BANK_STATEMENT');

    await waitFor(() => {
      expect(client.listImports).toHaveBeenLastCalledWith(
        expect.objectContaining({ source_system: 'BANK_STATEMENT' }),
      );
    });
  });

  it('passes a record type filter to the API', async () => {
    renderScreen(<ImportsPage />);
    await screen.findByLabelText('Record type');

    await userEvent.selectOptions(screen.getByLabelText('Record type'), 'PAYOUT');

    await waitFor(() => {
      expect(client.listImports).toHaveBeenLastCalledWith(
        expect.objectContaining({ record_type: 'PAYOUT' }),
      );
    });
  });

  it('sends no filter at all once one is cleared', async () => {
    renderScreen(<ImportsPage />);
    await screen.findByLabelText('Outcome');
    await userEvent.selectOptions(screen.getByLabelText('Outcome'), 'ACCEPTED');

    await userEvent.selectOptions(screen.getByLabelText('Outcome'), '');

    await waitFor(() => {
      expect(client.listImports).toHaveBeenLastCalledWith(
        expect.objectContaining({ outcome: undefined }),
      );
    });
  });

  it('reports a failure to load the history with a retry', async () => {
    client.listImports.mockRejectedValue(new NetworkError());
    renderScreen(<ImportsPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be reached/i);
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});

describe('a receipt with nothing to list', () => {
  it('shows the summary without an empty row table', async () => {
    client.importDocument.mockResolvedValue({
      ...INVALID_RECEIPT,
      row_count: 0,
      rejected_count: 0,
      not_applied_count: 0,
      row_outcomes: [],
      failure_detail: 'MISSING_HEADER: document is empty, so it has no header row',
    });
    renderScreen(<ImportsPage />);
    await screen.findByRole('button', { name: /import document/i });

    await uploadFile();

    expect(await screen.findByText(/document is empty/i)).toBeInTheDocument();
    expect(screen.queryByText(/what happened to each row/i)).not.toBeInTheDocument();
  });
});
