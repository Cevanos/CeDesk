import type { Me, Role } from "@/lib/api/types";

/** Role matrix. The UI only hides/disables things; the API still enforces with 403. */
const RANK: Record<Role, number> = {
  light_agent: 0,
  agent: 1,
  supervisor: 2,
  admin: 3,
};

const atLeast = (role: Role | undefined, min: Role) =>
  role !== undefined && RANK[role] >= RANK[min];

export function can(role: Role | undefined) {
  return {
    note: role !== undefined,
    reply: atLeast(role, "agent"),
    resolve: atLeast(role, "agent"),
    createTicket: atLeast(role, "agent"),
    editTicket: atLeast(role, "agent"),
    assignSelf: atLeast(role, "agent"),
    assignOthers: atLeast(role, "supervisor"),
    setOwnStatus: atLeast(role, "agent"),
    manageSettings: role === "admin",
    viewReports: atLeast(role, "supervisor"),
  };
}

/**
 * Whether GET /me's `access.actions` allows an action (`reply`, `approve`, `reports`...).
 * Older answers without `access` fall back to `fallback` (the role matrix above).
 */
export function hasAction(me: Me | undefined, action: string, fallback = false): boolean {
  const actions = me?.access?.actions;
  return Array.isArray(actions) ? actions.includes(action) : fallback;
}

/**
 * The three inbox-tool actions from GET /me `access.actions`. An older answer without `access`
 * falls back to the role matrix (bulk and transcript: agent and up; share views: supervisor up).
 */
export function inboxActions(me: Me | undefined) {
  const p = can(me?.role);
  return {
    bulkEdit: hasAction(me, "bulk_edit", p.editTicket),
    shareViews: hasAction(me, "share_views", p.assignOthers),
    sendTranscript: hasAction(me, "send_transcript", p.editTicket),
  };
}

export const ROLE_LABEL: Record<Role, string> = {
  light_agent: "Light agent",
  agent: "Agent",
  supervisor: "Supervisor",
  admin: "Admin",
};

/** Email campaigns and the suppression list: the `manage_campaigns` action, Admin only. */
export function canManageCampaigns(me: Me | undefined): boolean {
  return hasAction(me, "manage_campaigns", false);
}
