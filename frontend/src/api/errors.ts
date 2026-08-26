/**
 * The three ways a request to the backend can fail, told apart.
 *
 * They are separate types because the interface has to say something different
 * about each. A refusal from the API has a message written for people and
 * should be shown as it was written. A backend that cannot be reached is not
 * the user's mistake and needs a retry. A response that arrived but did not
 * make sense is a defect somewhere, and pretending it was empty data would put
 * a wrong number on screen instead of an error.
 */

/** A refusal the API returned, carrying its own code and message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** The backend could not be reached at all. */
export class NetworkError extends Error {
  constructor(cause?: unknown) {
    super('The backend could not be reached.', cause === undefined ? undefined : { cause });
    this.name = 'NetworkError';
  }
}

/** A response arrived and was not the shape this client knows how to read. */
export class MalformedResponseError extends Error {
  constructor(what: string) {
    super(`The backend returned a response this app could not read: ${what}.`);
    this.name = 'MalformedResponseError';
  }
}

/**
 * Return a sentence to show a person for any failure.
 *
 * API messages are passed through as written: the backend composes them for
 * people and they name the field or the rule that was refused. Anything else
 * gets a sentence from here, because an exception message is written for
 * whoever is reading a stack trace.
 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof NetworkError) {
    return 'The backend could not be reached. It may not be running.';
  }
  if (error instanceof MalformedResponseError) {
    return error.message;
  }
  return 'Something went wrong. Please try again.';
}

/**
 * Read the nested error envelope the API returns.
 *
 * Every failure arrives as `{"detail": {"error": ..., "detail": ...}}`. A body
 * that does not match, which is what a proxy or a crash would produce, falls
 * back to the status code rather than being reported as an empty message.
 */
export function toApiError(status: number, body: unknown): ApiError {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = body.detail;
    if (
      typeof detail === 'object' &&
      detail !== null &&
      'error' in detail &&
      'detail' in detail &&
      typeof (detail as { error: unknown }).error === 'string' &&
      typeof (detail as { detail: unknown }).detail === 'string'
    ) {
      const { error, detail: message } = detail as { error: string; detail: string };
      return new ApiError(status, error, message);
    }
    if (typeof detail === 'string') {
      return new ApiError(status, 'error', detail);
    }
  }
  return new ApiError(
    status,
    'error',
    `The backend refused the request with status ${String(status)}.`,
  );
}
