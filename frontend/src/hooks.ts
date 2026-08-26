/**
 * Loading something from the backend, with the three states that implies.
 */

import { useEffect, useState } from 'react';

export interface Loadable<T> {
  readonly data: T | null;
  readonly error: unknown;
  readonly loading: boolean;
  /** Run the request again. Bound to the retry button on every error state. */
  readonly reload: () => void;
}

interface Settled<T> {
  /** Which request this answer belongs to. */
  readonly request: string;
  readonly data: T | null;
  readonly error: unknown;
}

/**
 * Load a value when the component mounts, and again when the key changes.
 *
 * The caller passes a key describing what its request depends on, so what makes
 * one request different from another is stated rather than inferred from a
 * dependency array.
 *
 * Loading is derived rather than stored: an answer is stamped with the request
 * it belongs to, and the view is loading exactly while the stored answer
 * belongs to an older one. That keeps every state change inside the promise
 * callbacks, so nothing is set while the effect is still running, and it makes
 * a stale answer impossible to display rather than merely unlikely. Clicking a
 * filter twice quickly cannot leave the slower answer on screen, which here
 * would mean showing one filter's decisions under another's heading.
 */
export function useLoad<T>(load: () => Promise<T>, key: string): Loadable<T> {
  const [attempt, setAttempt] = useState(0);
  const [settled, setSettled] = useState<Settled<T> | null>(null);

  const request = `${key}#${String(attempt)}`;

  useEffect(() => {
    let current = true;
    load()
      .then((value) => {
        if (current) {
          setSettled({ request, data: value, error: null });
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setSettled({ request, data: null, error: cause });
        }
      });
    return () => {
      current = false;
    };
    // `load` is rebuilt every render and closes over the values the key
    // describes, so re-running on the request alone runs the current one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  const answered = settled?.request === request;
  return {
    data: answered ? settled.data : null,
    error: answered ? settled.error : null,
    loading: !answered,
    reload: () => {
      setAttempt((previous) => previous + 1);
    },
  };
}
