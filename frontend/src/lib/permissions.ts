import type { Role } from "@/lib/api/types";

/**
 * Role matrix from the guide (section 4). The UI hides or disables what a
 * role cannot do; the API still enforces it with 403.
 */
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
  };
}

export const ROLE_LABEL: Record<Role, string> = {
  light_agent: "Light agent",
  agent: "Agent",
  supervisor: "Supervisor",
  admin: "Admin",
};
