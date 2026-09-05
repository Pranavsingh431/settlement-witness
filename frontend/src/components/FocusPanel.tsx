import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

/** Move keyboard/visual attention to an opened case, including stacked mobile
 * layouts. Closing returns focus to the control that opened it, when it remains. */
export function FocusPanel({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.focus();
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus({ preventScroll: true });
    };
  }, []);
  return (
    <aside ref={ref} tabIndex={-1} className="case-panel" aria-label={label}>
      {children}
    </aside>
  );
}
