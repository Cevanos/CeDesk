"use client";

import { Inbox } from "lucide-react";
import { useState } from "react";

import {
  AiBadge,
  PRIORITIES,
  PriorityBadge,
  STATUSES,
  StatusBadge,
  VipBadge,
} from "@/components/tickets/badges";
import { Logo, ThemeToggle } from "@/components/ui/brand";
import { Dialog } from "@/components/ui/overlay";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-xs font-bold tracking-[0.08em] text-muted-foreground uppercase">
        {title}
      </h2>
      <Card className="flex flex-wrap items-center gap-3 p-5">{children}</Card>
    </section>
  );
}

/**
 * Living style guide for the UI primitives: every variant and state in one
 * place, in both themes. Development only (see app/dev/ui/page.tsx).
 */
export function UiGallery() {
  const [open, setOpen] = useState(false);
  const [priority, setPriority] = useState("medium");

  return (
    <div className="mx-auto max-w-4xl space-y-8 px-4 py-8">
      <header className="flex items-center justify-between">
        <Logo />
        <ThemeToggle />
      </header>
      <h1 className="text-2xl font-semibold tracking-tight">UI gallery</h1>

      <Section title="Button variants">
        {(["primary", "secondary", "outline", "ghost", "danger"] as const).map((v) => (
          <Button key={v} variant={v}>
            {v}
          </Button>
        ))}
      </Section>

      <Section title="Button sizes and states">
        <Button size="sm">Small</Button>
        <Button size="md">Medium</Button>
        <Button size="lg">Large</Button>
        <Button loading>Saving</Button>
        <Button disabled>Disabled</Button>
      </Section>

      <Section title="Fields">
        <div className="grid w-full gap-4 sm:grid-cols-2">
          <Field label="Name" htmlFor="g-name" hint="As it appears on invoices.">
            <Input id="g-name" placeholder="Ada Lovelace" />
          </Field>
          <Field label="Email" htmlFor="g-email" error="Enter a valid email">
            <Input id="g-email" aria-invalid defaultValue="nope" aria-describedby="g-email-error" />
          </Field>
          <Field label="Priority" htmlFor="g-priority">
            <Select id="g-priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Message" htmlFor="g-msg">
            <Textarea id="g-msg" placeholder="Write something…" />
          </Field>
        </div>
      </Section>

      <Section title="Badges">
        {PRIORITIES.map((p) => (
          <PriorityBadge key={p} priority={p} />
        ))}
        {STATUSES.map((s) => (
          <StatusBadge key={s} status={s} />
        ))}
        <VipBadge />
        <AiBadge source="ai" />
        {(["neutral", "primary", "success", "warning", "danger", "info"] as const).map((t) => (
          <Badge key={t} tone={t}>
            {t}
          </Badge>
        ))}
      </Section>

      <Section title="Loading and empty">
        <div className="w-full space-y-2">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-4 w-2/3" />
        </div>
        <div className="w-full">
          <EmptyState icon={<Inbox className="size-6" />} title="Inbox zero">
            No tickets in this tab right now.
          </EmptyState>
        </div>
      </Section>

      <Section title="Overlays">
        <Button variant="outline" onClick={() => setOpen(true)}>
          Open dialog
        </Button>
        <Dialog
          open={open}
          onClose={() => setOpen(false)}
          title="Example dialog"
          description="Focus is trapped; Esc closes."
        >
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => setOpen(false)}>Confirm</Button>
          </div>
        </Dialog>
      </Section>
    </div>
  );
}
