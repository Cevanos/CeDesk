"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, Field, Input, Skeleton, Textarea } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { parseIntIn, parseIpList } from "@/components/workspace/security-helpers";
import {
  useSaveSecuritySettings,
  useSaveSsoSettings,
  useSecurityScore,
  useSecuritySettings,
  useSsoSettings,
  useTestMyIp,
  type SecuritySettings,
  type SsoSettings,
} from "@/hooks/use-workspace";

export function SecurityView() {
  return (
    <div className="space-y-6">
      <ScoreCard />
      <SettingsCard />
      <SsoCard />
    </div>
  );
}

function ScoreCard() {
  const q = useSecurityScore();
  if (q.isPending) return <Skeleton className="h-24" />;
  if (q.isError) return <LoadError error={q.error} />;
  const s = q.data ?? {};
  const checks = Array.isArray(s.checks) ? s.checks : [];
  return (
    <Card className="p-5">
      <div className="flex items-baseline gap-2">
        <h2 className="text-[15px] font-semibold">Security score</h2>
        {typeof s.score === "number" && (
          <span className="text-2xl font-semibold tabular-nums">
            {s.score}
            {typeof s.max === "number" && (
              <span className="text-sm text-muted-foreground"> / {s.max}</span>
            )}
          </span>
        )}
      </div>
      {checks.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-sm">
          {checks.map((c, i) => {
            const ok = c.ok ?? c.passed ?? false;
            return (
              <li key={c.key ?? i} className="flex items-start gap-2">
                <Badge tone={ok ? "success" : "warning"}>{ok ? "Good" : "To do"}</Badge>
                <span>
                  {c.label ?? c.key}
                  {c.hint && !ok && <span className="text-muted-foreground"> — {c.hint}</span>}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function ToggleRow({
  id,
  title,
  body,
  checked,
  onChange,
}: {
  id: string;
  title: string;
  body: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
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
        labelledBy={`${id}-l`}
        describedBy={`${id}-d`}
      />
    </div>
  );
}

function SettingsCard() {
  const q = useSecuritySettings();
  if (q.isPending) return <Skeleton className="h-64" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <SettingsForm initial={q.data ?? {}} />;
}

function SettingsForm({ initial }: { initial: SecuritySettings }) {
  const save = useSaveSecuritySettings();
  const testIp = useTestMyIp();
  const [minLen, setMinLen] = useState(String(initial.min_password_length ?? ""));
  const [expiry, setExpiry] = useState(
    initial.password_expiry_days ? String(initial.password_expiry_days) : "",
  );
  const [session, setSession] = useState(String(initial.session_hours ?? ""));
  const [twoFa, setTwoFa] = useState(!!initial.require_2fa);
  const [ips, setIps] = useState((initial.ip_allowlist ?? []).join("\n"));
  const [filesAuth, setFilesAuth] = useState(!!initial.files_require_signin);
  const [alerts, setAlerts] = useState(!!initial.admin_alerts);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [ipTested, setIpTested] = useState<string | null>(null);

  const list = parseIpList(ips);

  function runTest() {
    testIp.mutate(list, {
      onSuccess: (r) => {
        const ok = r.allowed !== false;
        setIpTested(
          ok
            ? `Your address${r.ip ? ` (${r.ip})` : ""} is allowed. Safe to save.`
            : `Your address${r.ip ? ` (${r.ip})` : ""} is NOT on this list. Saving would lock you out.`,
        );
      },
      onError: (e) => setErrs(fieldErrors(e)),
    });
  }

  return (
    <Card className="p-5">
      <h2 className="mb-4 text-[15px] font-semibold">Security settings</h2>
      <form
        noValidate
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          const next: Record<string, string> = {};
          const min = minLen.trim() ? parseIntIn(minLen, 6, 128) : undefined;
          if (min === null) next.min_password_length = "Enter a number from 6 to 128";
          const exp = expiry.trim() ? parseIntIn(expiry, 1, 3650) : 0;
          if (exp === null) next.password_expiry_days = "Enter a number of days, or leave empty";
          const ses = session.trim() ? parseIntIn(session, 1, 8760) : undefined;
          if (ses === null) next.session_hours = "Enter a number of hours";
          setErrs(next);
          if (Object.keys(next).length) return;
          if (
            list.length > 0 &&
            ipTested === null &&
            !confirm("You have not tested your own IP against this list. Save anyway?")
          )
            return;
          save.mutate(
            {
              ...initial,
              ...(min !== undefined && { min_password_length: min }),
              password_expiry_days: exp || null,
              ...(ses !== undefined && { session_hours: ses }),
              require_2fa: twoFa,
              ip_allowlist: list,
              files_require_signin: filesAuth,
              admin_alerts: alerts,
            },
            {
              onSuccess: () => toast.success("Security settings saved"),
              onError: (err) => setErrs(fieldErrors(err)),
            },
          );
        }}
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Minimum password length" htmlFor="sec-min" error={errs.min_password_length}>
            <Input
              id="sec-min"
              inputMode="numeric"
              value={minLen}
              onChange={(e) => setMinLen(e.target.value)}
            />
          </Field>
          <Field
            label="Password expires after (days)"
            htmlFor="sec-exp"
            error={errs.password_expiry_days}
            hint="Empty means never."
          >
            <Input
              id="sec-exp"
              inputMode="numeric"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
            />
          </Field>
          <Field label="Session length (hours)" htmlFor="sec-ses" error={errs.session_hours}>
            <Input
              id="sec-ses"
              inputMode="numeric"
              value={session}
              onChange={(e) => setSession(e.target.value)}
            />
          </Field>
        </div>

        <ToggleRow
          id="sec-2fa"
          title="Require two-factor sign-in"
          body="Everyone on the team must use a second step to sign in."
          checked={twoFa}
          onChange={setTwoFa}
        />
        <ToggleRow
          id="sec-files"
          title="Files need sign-in"
          body="Attachments can only be opened by signed-in people."
          checked={filesAuth}
          onChange={setFilesAuth}
        />
        <ToggleRow
          id="sec-alerts"
          title="Alert admins about security events"
          body="Admins get an email when something looks wrong."
          checked={alerts}
          onChange={setAlerts}
        />

        <Field
          label="Allowed IP addresses"
          htmlFor="sec-ips"
          error={errs.ip_allowlist}
          hint="One address or range (like 203.0.113.0/24) per line. Empty allows everyone."
        >
          <Textarea
            id="sec-ips"
            rows={4}
            value={ips}
            onChange={(e) => {
              setIps(e.target.value);
              setIpTested(null);
            }}
            spellCheck={false}
            className="font-mono"
          />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" loading={testIp.isPending} onClick={runTest}>
            Test my IP
          </Button>
          {ipTested && (
            <p role="status" className="text-sm text-muted-foreground">
              {ipTested}
            </p>
          )}
        </div>

        <FormError message={errs._form} />
        <div className="flex justify-end">
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Card>
  );
}

function SsoCard() {
  const q = useSsoSettings();
  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <LoadError error={q.error} />;
  return <SsoForm initial={q.data ?? {}} />;
}

function SsoForm({ initial }: { initial: SsoSettings }) {
  const save = useSaveSsoSettings();
  const [enabled, setEnabled] = useState(!!initial.enabled);
  const [issuer, setIssuer] = useState(initial.issuer ?? "");
  const [clientId, setClientId] = useState(initial.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <Card className="p-5">
      <h2 className="mb-4 text-[15px] font-semibold">Single sign-on</h2>
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const body: SsoSettings = {
            ...initial,
            enabled,
            issuer: issuer.trim(),
            client_id: clientId.trim(),
          };
          delete body.secret_hint;
          if (secret) body.client_secret = secret;
          else delete body.client_secret;
          save.mutate(body, {
            onSuccess: () => {
              setSecret("");
              toast.success("Single sign-on saved");
            },
            onError: (err) => setErrs(fieldErrors(err)),
          });
        }}
      >
        <ToggleRow
          id="sso-on"
          title="Enable single sign-on"
          body="Let people sign in with your identity provider."
          checked={enabled}
          onChange={setEnabled}
        />
        <Field label="Issuer URL" htmlFor="sso-issuer" error={errs.issuer}>
          <Input
            id="sso-issuer"
            value={issuer}
            onChange={(e) => setIssuer(e.target.value)}
            placeholder="https://login.example.com"
          />
        </Field>
        <Field label="Client ID" htmlFor="sso-cid" error={errs.client_id}>
          <Input id="sso-cid" value={clientId} onChange={(e) => setClientId(e.target.value)} />
        </Field>
        <Field
          label="Client secret"
          htmlFor="sso-secret"
          error={errs.client_secret}
          hint={
            initial.secret_hint
              ? `Saved (${initial.secret_hint}). Leave empty to keep it.`
              : "Never shown again after saving."
          }
        >
          <Input
            id="sso-secret"
            type="password"
            autoComplete="new-password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
        </Field>
        <FormError message={errs._form} />
        <div className="flex justify-end">
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Card>
  );
}
