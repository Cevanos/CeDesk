"use client";

import { ChevronDown, Loader2, MailCheck, Network, Send } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/ui/copy-button";
import { Badge, Button, Input } from "@/components/ui/primitives";
import { useDnsCheck, useForwardingCheck, useSaveDkimSelector } from "@/hooks/use-workspace";
import type { DnsCheck, EmailAddress } from "@/lib/api/types";
import { cn, timeAgo } from "@/lib/utils";

type Tone = "success" | "warning" | "danger" | "neutral" | "info";

const DNS_TONE: Record<string, Tone> = {
  pass: "success",
  warn: "warning",
  fail: "danger",
  missing: "danger",
};
const DNS_LABEL: Record<string, string> = {
  pass: "Pass",
  warn: "Needs attention",
  fail: "Fail",
  missing: "Missing",
  not_checked: "Not checked",
};

/** "Verified / Not checked yet / Waiting" badge for the forwarding row. */
export function ForwardingBadge({ status }: { status?: string | null }) {
  if (status === "verified") return <Badge tone="success">Verified</Badge>;
  if (status === "waiting")
    return (
      <Badge tone="info">
        <Loader2 className="size-3 animate-spin" aria-hidden /> Waiting for the check mail
      </Badge>
    );
  return <Badge>Not checked yet</Badge>;
}

/** Short status for the collapsed header, so problems are visible without opening it. */
export function checksSummary(a: EmailAddress): { tone: Tone; text: string } {
  const dns = [a.dns?.spf, a.dns?.dkim, a.dns?.dmarc];
  if (dns.some((d) => d && (d.status === "fail" || d.status === "missing")))
    return { tone: "danger", text: "DNS needs fixing" };
  if (a.forwarding?.status === "waiting") return { tone: "info", text: "Waiting for check mail" };
  if (a.forwarding?.status === "verified" && dns.every((d) => d?.status === "pass"))
    return { tone: "success", text: "All checks pass" };
  if (dns.some((d) => d?.status === "warn")) return { tone: "warning", text: "Needs attention" };
  return { tone: "neutral", text: "Not fully checked" };
}

function Mono({ text, label }: { text: string; label: string }) {
  return (
    <div className="mt-1.5 flex items-start gap-1 rounded-lg bg-surface-2 py-1.5 pr-1 pl-3">
      <code className="min-w-0 flex-1 font-mono text-xs leading-5 break-all">{text}</code>
      <CopyButton text={text} label={label} />
    </div>
  );
}

function DnsRow({ name, check }: { name: string; check?: DnsCheck | null }) {
  const status = check?.status ?? "not_checked";
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-14 font-mono text-[13px] font-semibold">{name}</span>
        <Badge tone={DNS_TONE[status] ?? "neutral"}>{DNS_LABEL[status] ?? status}</Badge>
      </div>
      {check?.note && <p className="mt-1.5 text-[13px] text-muted-foreground">{check.note}</p>}
      {check?.record && <Mono text={check.record} label={`Copy ${name} record`} />}
    </li>
  );
}

/** Forwarding + DNS checks for one support address, in a collapsible panel. */
export function EmailChecks({ address: a }: { address: EmailAddress }) {
  const [open, setOpen] = useState(false);
  const forwarding = useForwardingCheck();
  const dns = useDnsCheck();
  const summary = checksSummary(a);
  const panelId = `checks-${a.id}`;

  const f = a.forwarding;
  const waiting = f?.status === "waiting";
  const from = f?.forward_from ?? a.address;
  const forwardTo = a.forward_to ?? f?.forward_to;

  return (
    <div className="basis-full">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-lg px-1 py-1 text-left text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
        Deliverability
        <Badge tone={summary.tone}>{summary.text}</Badge>
      </button>

      {open && (
        <div id={panelId} className="mt-2 grid animate-rise gap-3 lg:grid-cols-2">
          <section
            aria-label="Forwarding"
            className="rounded-xl border border-border bg-surface-2/40 p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <MailCheck className="size-4 text-primary-ink" aria-hidden />
              <h4 className="text-sm font-semibold">Forwarding</h4>
              <ForwardingBadge status={f?.status} />
            </div>
            <p className="mt-2 text-[13px] text-muted-foreground">
              Set your mail provider to forward your support address to CeDesk, so customer emails
              become tickets.
            </p>
            {forwardTo ? (
              <div className="mt-2">
                <p className="text-[13px]">
                  Forward <strong>{from}</strong> to
                </p>
                <Mono text={forwardTo} label={`Copy ${forwardTo}`} />
              </div>
            ) : (
              <p className="mt-2 text-[13px] text-muted-foreground">
                The forwarding address will show here once it is ready.
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                variant="outline"
                loading={forwarding.isPending}
                disabled={waiting}
                onClick={() =>
                  forwarding.mutate(a.id, {
                    onSuccess: () =>
                      toast.success("Check email sent. This page updates when it arrives."),
                  })
                }
              >
                <Send className="size-3.5" />{" "}
                {f?.status === "verified" ? "Check again" : "Send check"}
              </Button>
              {waiting && (
                <span role="status" className="text-xs text-muted-foreground">
                  Looking for the check mail. This can take a minute.
                </span>
              )}
              {f?.checked_at && !waiting && (
                <span className="text-xs text-subtle-foreground">
                  Last checked {timeAgo(f.checked_at)}
                </span>
              )}
            </div>
          </section>

          <section aria-label="DNS" className="rounded-xl border border-border bg-surface-2/40 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Network className="size-4 text-primary-ink" aria-hidden />
              <h4 className="text-sm font-semibold">DNS records</h4>
              <Button
                size="sm"
                variant="outline"
                className="ml-auto"
                loading={dns.isPending}
                onClick={() => dns.mutate(a.id, { onSuccess: () => toast.success("DNS checked") })}
              >
                Check DNS
              </Button>
            </div>
            <ul className="mt-3 divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
              <DnsRow name="SPF" check={a.dns?.spf} />
              <DnsRow name="DKIM" check={a.dns?.dkim} />
              <DnsRow name="DMARC" check={a.dns?.dmarc} />
            </ul>
            {a.dns?.checked_at && (
              <p className="mt-2 text-xs text-subtle-foreground">
                Last checked {timeAgo(a.dns.checked_at)}
              </p>
            )}
            <DkimSelector key={a.dkim_selector ?? ""} address={a} />
          </section>
        </div>
      )}
    </div>
  );
}

function DkimSelector({ address: a }: { address: EmailAddress }) {
  const save = useSaveDkimSelector();
  const [value, setValue] = useState(a.dkim_selector ?? "");
  const dirty = value.trim() !== (a.dkim_selector ?? "");
  const id = `dkim-${a.id}`;
  return (
    <form
      className="mt-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(
          { id: a.id, dkim_selector: value.trim() },
          { onSuccess: () => toast.success("DKIM selector saved") },
        );
      }}
    >
      <label htmlFor={id} className="text-[13px] font-medium text-foreground-2">
        DKIM selector
      </label>
      <div className="mt-1.5 flex gap-2">
        <Input
          id={id}
          value={value}
          maxLength={63}
          placeholder="e.g. google or selector1"
          onChange={(e) => setValue(e.target.value)}
        />
        <Button type="submit" variant="outline" disabled={!dirty} loading={save.isPending}>
          Save
        </Button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        The name your mail provider gave its DKIM key. We look the record up under it.
      </p>
    </form>
  );
}
