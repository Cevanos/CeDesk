"use client";

import { Eye, EyeOff, Plus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button, Select, Skeleton } from "@/components/ui/primitives";
import { useCurrentUser } from "@/hooks/use-auth";
import { useAddFollower, useFollowers, useRemoveFollower } from "@/hooks/use-inbox-tools";
import { useMemberOptions } from "@/hooks/use-workspace";
import { initials } from "@/lib/utils";

/** "Follow" / "Following" toggle for the ticket header. Anyone who can see the ticket may use it. */
export function FollowButton({ ticketId }: { ticketId: string }) {
  const { user } = useCurrentUser();
  const followers = useFollowers(ticketId);
  const add = useAddFollower(ticketId);
  const remove = useRemoveFollower(ticketId);
  const following = followers.data?.following ?? false;
  const busy = add.isPending || remove.isPending;

  if (!user) return null;
  return (
    <Button
      variant="outline"
      aria-pressed={following}
      disabled={followers.isPending}
      loading={busy}
      onClick={() =>
        following
          ? remove.mutate(user.id, { onSuccess: () => toast.success("You stopped following") })
          : add.mutate(user.id, { onSuccess: () => toast.success("You are following this ticket") })
      }
    >
      {!busy &&
        (following ? (
          <Eye className="size-4 text-primary" aria-hidden />
        ) : (
          <EyeOff className="size-4" aria-hidden />
        ))}{" "}
      {following ? "Following" : "Follow"}
    </Button>
  );
}

/** Followers in the ticket side panel. `canEdit`: may add others and remove anyone. */
export function FollowersList({ ticketId, canEdit }: { ticketId: string; canEdit: boolean }) {
  const { user } = useCurrentUser();
  const q = useFollowers(ticketId);
  const add = useAddFollower(ticketId);
  const remove = useRemoveFollower(ticketId);
  const [adding, setAdding] = useState(false);
  const members = useMemberOptions(adding);

  const followers = q.data?.followers ?? [];
  const taken = new Set(followers.map((f) => f.member_id));
  const options = (members.data ?? []).filter((m) => m.is_active && !taken.has(m.id));

  if (q.isPending) return <Skeleton className="h-8" />;
  if (q.isError) return <p className="text-sm text-danger">Couldn&rsquo;t load followers.</p>;

  return (
    <div>
      {followers.length === 0 ? (
        <p className="text-sm text-muted-foreground">No followers yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {followers.map((f) => {
            const mine = f.member_id === user?.id;
            return (
              <li
                key={f.member_id}
                className="flex items-center gap-2.5"
                title={f.added_by_name ? `Added by ${f.added_by_name}` : undefined}
              >
                <span
                  aria-hidden
                  className="grid size-7 flex-none place-items-center rounded-full bg-primary-soft text-[10px] font-bold text-primary-ink"
                >
                  {initials(f.name)}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm">
                  {f.name}
                  {mine && <span className="text-muted-foreground"> (you)</span>}
                  {f.is_active === false && (
                    <span className="text-muted-foreground"> · inactive</span>
                  )}
                </span>
                {(canEdit || mine) && (
                  <button
                    aria-label={mine ? "Stop following" : `Remove ${f.name} as follower`}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(f.member_id)}
                    className="grid size-6 flex-none place-items-center rounded-md text-subtle-foreground hover:bg-surface-2 hover:text-foreground disabled:opacity-50"
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {canEdit &&
        (adding ? (
          <div className="mt-3 flex items-center gap-2">
            <Select
              aria-label="Add follower"
              className="min-w-0 flex-1"
              searchable
              defaultOpen
              placeholder={members.isPending ? "Loading…" : "Pick a person"}
              value=""
              onChange={(e) => {
                if (!e.target.value) return;
                add.mutate(e.target.value, { onSuccess: () => setAdding(false) });
              }}
            >
              {options.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </Select>
            <button
              aria-label="Cancel"
              onClick={() => setAdding(false)}
              className="grid size-8 flex-none place-items-center rounded-lg text-muted-foreground hover:bg-surface-2"
            >
              <X className="size-4" />
            </button>
          </div>
        ) : (
          <button
            onClick={() => setAdding(true)}
            className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-primary-ink hover:underline"
          >
            <Plus className="size-4" aria-hidden /> Add follower
          </button>
        ))}
    </div>
  );
}
