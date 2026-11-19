"use client";

import { BookOpen, ExternalLink, MessageSquareText, Star, Ticket } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/ui/copy-button";
import { Button, Card, Skeleton } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { TagInput } from "@/components/ui/tag-input";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { usePortalSettings, useSavePortalSettings } from "@/hooks/use-workspace";
import type { PortalSettings } from "@/lib/api/types";

/** `@Example.com` -> `example.com`. */
export const cleanDomain = (tag: string) => tag.replace(/^@+/, "");

const DOMAIN = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

/** Error text for something that isn't a domain like `example.com`, or null when it is. */
export function validateDomain(tag: string): string | null {
  return DOMAIN.test(tag) ? null : `“${tag}” isn't a domain. Use something like example.com.`;
}

export function PortalSettingsView() {
  const q = usePortalSettings();
  if (q.isPending) return <Skeleton className="h-64" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <PortalBody key={JSON.stringify(q.data)} saved={q.data} />;
}

function PortalBody({ saved }: { saved: PortalSettings }) {
  const save = useSavePortalSettings();
  const [domains, setDomains] = useState(saved.allowed_domains);
  const [csat, setCsat] = useState(saved.csat_survey);
  const [form, setForm] = useState(saved.web_form);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [tagKey, setTagKey] = useState(0);

  const dirty =
    csat !== saved.csat_survey ||
    form !== saved.web_form ||
    JSON.stringify(domains) !== JSON.stringify(saved.allowed_domains);
  useUnsavedChanges(dirty);

  return (
    <div className="space-y-6">
      <PublicLinks />

      <section aria-labelledby="portal-domains">
        <h2 id="portal-domains" className="text-base font-semibold">
          Who can sign in
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Customers sign in to the portal with a 6-digit code sent to their email. Leave it empty to
          let anyone sign in. Add domains (like acme.com) to allow only those emails.
        </p>
        <Card className="mt-3 p-4">
          <TagInput
            key={tagKey}
            label="Allowed email domains"
            tags={domains}
            placeholder="example.com"
            transform={cleanDomain}
            validate={validateDomain}
            onChange={(d) => {
              setDomains(d);
              setErrs((x) => ({ ...x, allowed_domains: "" }));
            }}
          />
          {errs.allowed_domains && (
            <p role="alert" className="mt-1 text-xs text-danger">
              {errs.allowed_domains}
            </p>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            Customers signed in with other emails are signed out when you save.
          </p>
        </Card>
      </section>

      <section aria-labelledby="portal-features">
        <h2 id="portal-features" className="text-base font-semibold">
          Features
        </h2>
        <Card className="mt-3 divide-y divide-border">
          <ToggleRow
            id="csat"
            icon={<Star className="size-4" />}
            title="CSAT survey"
            body="After a ticket is resolved, the customer's email asks them to rate the help from 1 to 5."
            checked={csat}
            onChange={setCsat}
          />
          <ToggleRow
            id="form"
            icon={<MessageSquareText className="size-4" />}
            title="Web form"
            body="A public “Contact us” page where anyone can open a ticket without signing in."
            checked={form}
            onChange={setForm}
          />
        </Card>
      </section>

      <FormError message={errs._form} />
      <div className="flex justify-end gap-3">
        <Button
          variant="ghost"
          disabled={!dirty || save.isPending}
          onClick={() => {
            setDomains(saved.allowed_domains);
            setCsat(saved.csat_survey);
            setForm(saved.web_form);
            setErrs({});
            setTagKey((k) => k + 1); // remounts the domain box: drops typed text and its error
          }}
        >
          Discard
        </Button>
        <Button
          disabled={!dirty}
          loading={save.isPending}
          onClick={() =>
            save.mutate(
              { allowed_domains: domains, csat_survey: csat, web_form: form },
              {
                onSuccess: () => toast.success("Portal settings saved"),
                onError: (e) => setErrs(fieldErrors(e)),
              },
            )
          }
        >
          Save changes
        </Button>
      </div>
    </div>
  );
}

function ToggleRow({
  id,
  icon,
  title,
  body,
  checked,
  onChange,
}: {
  id: string;
  icon: React.ReactNode;
  title: string;
  body: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-3.5 p-4">
      <span className="grid size-9 flex-none place-items-center rounded-xl bg-primary-soft text-primary-ink">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p id={`${id}-l`} className="text-sm font-medium">
          {title}
        </p>
        <p id={`${id}-d`} className="mt-0.5 text-sm text-muted-foreground">
          {body}
        </p>
      </div>
      <Switch
        checked={checked}
        onChange={onChange}
        label={title}
        labelledBy={`${id}-l`}
        describedBy={`${id}-d`}
      />
    </div>
  );
}

/** The three public addresses, built from the workspace slug and this site's origin. */
function PublicLinks() {
  const { user } = useCurrentUser();
  const slug = user?.workspace;
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  if (!slug) return null;

  const links = [
    { icon: Ticket, label: "Customer portal", path: `/${slug}/portal` },
    { icon: MessageSquareText, label: "Web form", path: `/${slug}/contact` },
    { icon: BookOpen, label: "Help center", path: `/${slug}/help` },
  ];
  return (
    <section aria-labelledby="portal-links">
      <h2 id="portal-links" className="text-base font-semibold">
        Public links
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Share these with your customers. None of them needs an agent login.
      </p>
      <Card className="mt-3 divide-y divide-border">
        {links.map(({ icon: Icon, label, path }) => {
          const url = `${origin}${path}`;
          return (
            <div key={path} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <Icon className="size-4 text-primary-ink" aria-hidden />
              <span className="w-32 text-sm font-medium">{label}</span>
              <code className="min-w-0 flex-1 basis-48 font-mono text-xs break-all text-muted-foreground">
                {url}
              </code>
              <CopyButton text={url} label={`Copy ${label} link`} />
              <a
                href={path}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Open ${label}`}
                className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
              >
                <ExternalLink className="size-3.5" />
              </a>
            </div>
          );
        })}
      </Card>
    </section>
  );
}
