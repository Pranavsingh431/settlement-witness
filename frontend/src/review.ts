/**
 * The words this application uses for review actions and workflow states.
 *
 * In their own module rather than beside the components, so the components file
 * exports only components. Both maps are indexed by string rather than by the
 * union type, for the same reason the badge maps are: the backend owns these
 * vocabularies, and a value this build has not heard of must still render as
 * something a person can read rather than as a blank.
 */

export interface ActionLabel {
  readonly label: string;
  readonly what: string;
}

/**
 * What each action is called on screen, and what recording it means.
 *
 * The descriptions are shown, not hidden behind a tooltip. The difference
 * between "this is closed" and "this is resolved" is the difference this whole
 * screen exists to make, and a difference that only appears on hover is a
 * difference most people never see.
 */
export const ACTION_LABELS: Record<string, ActionLabel> = {
  ACKNOWLEDGED: {
    label: 'Acknowledge',
    what: 'Record that somebody has seen this and is looking at it.',
  },
  REQUEST_EVIDENCE: {
    label: 'Request evidence',
    what: 'Record that more records are needed. Whatever arrives is imported and reconciled into a new run.',
  },
  ESCALATED: {
    label: 'Escalate',
    what: 'Record that this has been passed on, inside or outside this system.',
  },
  CLOSED_WITHOUT_OVERRIDE: {
    label: 'Close without override',
    what: 'Record that no further work is planned. The line keeps the status the baseline gave it.',
  },
};

export const WORKFLOW_LABELS: Record<string, string> = {
  OPEN: 'Not yet picked up',
  ACKNOWLEDGED: 'Acknowledged',
  WAITING_FOR_EVIDENCE: 'Waiting for evidence',
  ESCALATED: 'Escalated',
  CLOSED_WITHOUT_OVERRIDE: 'Closed without override',
};
