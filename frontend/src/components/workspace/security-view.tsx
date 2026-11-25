"use client";

import { Check, Minus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/ui/copy-button";
import { Button, Card, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
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
  type ScoreLevel,
  type SecuritySettings,
  type SecuritySettingsInput,
  type SessionLength,
  type SsoSettings,
} from "@/hooks/use-workspace";
import { bind, bindSelect, fieldError, useFormik, yup } from "@/lib/forms";

export function SecurityView() {
  return (
    <div className="space-y-6">
      <ScoreCard />
      <SettingsCard />
      <SsoCard />
    </div>
  );
}

const ITEM_LABEL: Record<string, string> = {
  sso: "Single sign-on",
  two_factor: "Two-factor sign-in for everyone",
  ip_allowlist: "IP allowlist",
  passwords: "Strong password rules",
  redaction: "Sensitive data hidden",
  session_length: "Short sign-in sessions",
  attachments_sign_in: "Files need sign-in",
  admin_alerts: "Email Admins about security events",
  portal_domains: "Customer portal limited to your domains",
};

const LEVEL: Record<ScoreLevel, { label: string; stroke: string; text: string }> = {
  healthy: { label: "Healthy", stroke: "stroke-success", text: "text-success" },
  recommended: { label: "Could be stronger", stroke: "stroke-warning", text: "text-warning" },
  critical: { label: "Needs attention", stroke: "stroke-danger", text: "text-danger" },
};

function ScoreCard() {
  const q = useSecurityScore();
  if (q.isPending) return <Skeleton className="h-24" />;
  if (q.isError) return <LoadError error={q.error} />;
  const s = q.data;
  const score = Math.max(0, Math.min(100, s.score));
  const level = LEVEL[s.level] ?? LEVEL.recommended;
  const r = 32;
  const c = 2 * Math.PI * r;
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-5">
        <div className="relative size-20 flex-none">
          <svg viewBox="0 0 80 80" className="size-20 -rotate-90" aria-hidden>
            <circle
              cx="40"
              cy="40"
              r={r}
              fill="none"
              strokeWidth="8"
              className="stroke-surface-3"
            />
            <circle
              cx="40"
              cy="40"
              r={r}
              fill="none"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={c}
              strokeDashoffset={c * (1 - score / 100)}
              className={level.stroke}
            />
          </svg>
          <span className="absolute inset-0 grid place-items-center text-xl font-semibold tabular-nums">
            {score}
          </span>
        </div>
        <div>
          <h2 className="text-[15px] font-semibold">Security score</h2>
          <p className={`text-sm font-medium ${level.text}`}>{level.label}</p>
          <p className="text-sm text-muted-foreground">Out of 100. It updates when you save.</p>
        </div>
      </div>
      <ul className="mt-4 grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
        {s.items.map((i) => (
          <li key={i.key} className="flex items-center gap-2">
            {i.on ? (
              <Check className="size-4 flex-none text-success" aria-label="On" />
            ) : (
              <Minus className="size-4 flex-none text-muted-foreground" aria-label="Off" />
            )}
            <span className={i.on ? "" : "text-muted-foreground"}>
              {ITEM_LABEL[i.key] ?? i.key.replace(/_/g, " ")}
            </span>
          </li>
        ))}
      </ul>
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
  return <SettingsForm initial={q.data} />;
}

const SESSION_OPTIONS: { value: SessionLength; label: string }[] = [
  { value: "1h_idle", label: "1 hour idle" },
  { value: "8h", label: "8 hours" },
  { value: "12h", label: "12 hours" },
  { value: "7d", label: "7 days" },
];
const EXPIRY_OPTIONS = [
  { value: "", label: "Never" },
  { value: "90", label: "90 days" },
  { value: "180", label: "180 days" },
];

const sameList = (a: string[], b: string[]) =>
  a.length === b.length && a.every((v, i) => v === b[i]);

function SettingsForm({ initial }: { initial: SecuritySettings }) {
  const save = useSaveSecuritySettings();
  const testIp = useTestMyIp();
  const [srv, setSrv] = useState<Record<string, string>>({});
  const [ipResult, setIpResult] = useState<{ ip: string; allowed: boolean } | null>(null);
  const form = useFormik({
    initialValues: {
      password_min: String(initial.password_min),
      expiry: initial.password_expiry_days ? String(initial.password_expiry_days) : "",
      session: initial.session_length as SessionLength,
      two_fa: initial.require_2fa,
      ips: initial.ip_allowlist.join("\n"),
      files_auth: initial.attachments_need_sign_in,
      alerts: initial.admin_alerts,
    },
    validate: (v) =>
      parseIntIn(v.password_min, 8, 64) === null
        ? { password_min: "Minimum password length is 8 to 64." }
        : {},
    onSubmit: (v) => {
      setSrv({});
      const min = parseIntIn(v.password_min, 8, 64);
      if (min === null) return;
      const ipList = parseIpList(v.ips);
      // Send only what changed; anything left out keeps its value.
      const body: SecuritySettingsInput = {};
      if (min !== initial.password_min) body.password_min = min;
      const exp = v.expiry ? Number(v.expiry) : null;
      if (exp !== initial.password_expiry_days) body.password_expiry_days = exp;
      if (v.session !== initial.session_length) body.session_length = v.session;
      if (v.two_fa !== initial.require_2fa) body.require_2fa = v.two_fa;
      if (v.files_auth !== initial.attachments_need_sign_in)
        body.attachments_need_sign_in = v.files_auth;
      if (v.alerts !== initial.admin_alerts) body.admin_alerts = v.alerts;
      if (!sameList(ipList, initial.ip_allowlist)) body.ip_allowlist = ipList;
      if (Object.keys(body).length === 0) {
        toast.info("Nothing has changed.");
        return;
      }

      save.mutate(body, {
        onSuccess: () => toast.success("Security settings saved"),
        // 422 messages come back ready to show, such as the "your own address" warning.
        onError: (err) => setSrv(fieldErrors(err)),
      });
    },
  });
  const errs: Record<string, string | undefined> = {
    ...srv,
    password_min: fieldError(form, "password_min") ?? srv.password_min,
  };

  const list = parseIpList(form.values.ips);

  function runTest() {
    setSrv({});
    // An empty box tests the saved list.
    testIp.mutate(list.length ? list : undefined, {
      onSuccess: setIpResult,
      onError: (e) => setSrv(fieldErrors(e)),
    });
  }

  return (
    <Card className="p-5">
      <h2 className="mb-4 text-[15px] font-semibold">Security settings</h2>
      <form noValidate className="space-y-5" onSubmit={form.handleSubmit}>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Minimum password length"
            htmlFor="sec-min"
            error={errs.password_min}
            hint="8 to 64."
          >
            <Input id="sec-min" inputMode="numeric" {...bind(form, "password_min")} />
          </Field>
          <Field label="Password expires" htmlFor="sec-exp" error={errs.password_expiry_days}>
            <Select id="sec-exp" {...bindSelect(form, "expiry")} options={EXPIRY_OPTIONS} />
          </Field>
          <Field label="Sign people out after" htmlFor="sec-ses" error={errs.session_length}>
            <Select id="sec-ses" {...bindSelect(form, "session")} options={SESSION_OPTIONS} />
          </Field>
        </div>

        <ToggleRow
          id="sec-2fa"
          title="Require two-factor sign-in"
          body="Everyone on the team must use a second step to sign in."
          checked={form.values.two_fa}
          onChange={(v) => form.setFieldValue("two_fa", v)}
        />
        <ToggleRow
          id="sec-files"
          title="Files need sign-in"
          body="Attachments can only be opened by signed-in people."
          checked={form.values.files_auth}
          onChange={(v) => form.setFieldValue("files_auth", v)}
        />
        <ToggleRow
          id="sec-alerts"
          title="Email Admins about security events"
          body="Admins get an email when something looks wrong."
          checked={form.values.alerts}
          onChange={(v) => form.setFieldValue("alerts", v)}
        />

        <Field
          label="IP allowlist"
          htmlFor="sec-ips"
          error={errs.ip_allowlist}
          hint="One address or range (like 203.0.113.0/24) per line, up to 100. Empty allows everyone."
        >
          <Textarea
            id="sec-ips"
            rows={4}
            {...bind(form, "ips")}
            onChange={(e) => {
              form.handleChange(e);
              setIpResult(null);
            }}
            spellCheck={false}
            className="font-mono"
          />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" loading={testIp.isPending} onClick={runTest}>
            Test my IP
          </Button>
          {ipResult && (
            <p
              role="status"
              className={`text-sm font-medium ${ipResult.allowed ? "text-success" : "text-danger"}`}
            >
              {ipResult.allowed
                ? `Your address ${ipResult.ip} is allowed.`
                : `Your address ${ipResult.ip} is NOT in the list.`}
            </p>
          )}
        </div>

        {/* Shows every 422 message as it comes, including ones that name no field. */}
        <FormError message={errs._form} />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            {initial.updated_at
              ? `Last changed${initial.updated_by_name ? ` by ${initial.updated_by_name}` : ""}, ${new Date(initial.updated_at).toLocaleString()}`
              : "Not changed yet."}
          </p>
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
  return <SsoForm initial={q.data} />;
}

function CopyRow({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <div>
      <p className="text-[13px] font-medium text-foreground-2">{label}</p>
      <div className="mt-1 flex items-center gap-1 rounded-lg border border-border bg-surface-2 px-3 py-1.5">
        <code className="min-w-0 flex-1 truncate font-mono text-xs">{value}</code>
        <CopyButton text={value} label={`Copy ${label}`} />
      </div>
    </div>
  );
}

function SsoForm({ initial }: { initial: SsoSettings }) {
  const save = useSaveSsoSettings();
  const [srv, setSrv] = useState<Record<string, string>>({});
  const form = useFormik({
    initialValues: {
      enabled: initial.enabled,
      enforced: initial.enforced,
      idp_entity_id: initial.idp_entity_id ?? "",
      idp_sso_url: initial.idp_sso_url ?? "",
      idp_cert: initial.idp_cert ?? "",
    },
    validationSchema: yup.object({
      idp_sso_url: yup
        .string()
        .trim()
        .matches(/^(https?:\/\/\S+)?$/i, "Enter a full URL starting with https://"),
    }),
    onSubmit: (v) => {
      setSrv({});
      save.mutate(
        {
          enabled: v.enabled,
          enforced: v.enforced,
          idp_entity_id: v.idp_entity_id.trim() || null,
          idp_sso_url: v.idp_sso_url.trim() || null,
          idp_cert: v.idp_cert.trim() || null,
        },
        {
          onSuccess: () => toast.success("Single sign-on saved"),
          onError: (err) => setSrv(fieldErrors(err)),
        },
      );
    },
  });
  const errs: Record<string, string | undefined> = {
    ...srv,
    idp_sso_url: fieldError(form, "idp_sso_url") ?? srv.idp_sso_url,
  };

  return (
    <Card className="p-5">
      <h2 className="mb-1 text-[15px] font-semibold">Single sign-on (SAML)</h2>
      <p className="mb-4 text-sm text-muted-foreground">
        Let people sign in with your identity provider. The sign-in page already shows &ldquo;Sign
        in with SSO&rdquo;.
      </p>

      <div className="mb-5 space-y-3 rounded-xl border border-border p-4">
        <h3 className="text-sm font-semibold">Give these to your identity provider</h3>
        <CopyRow label="Service provider entity ID" value={initial.sp_entity_id} />
        <CopyRow label="ACS URL" value={initial.acs_url} />
        <CopyRow label="Metadata URL" value={initial.metadata_url} />
      </div>

      <form noValidate className="space-y-4" onSubmit={form.handleSubmit}>
        <ToggleRow
          id="sso-on"
          title="Enable single sign-on"
          body="Let people sign in with your identity provider."
          checked={form.values.enabled}
          onChange={(v) => form.setFieldValue("enabled", v)}
        />
        <ToggleRow
          id="sso-enforced"
          title="Only allow SSO"
          body="Password sign-in is turned off for everyone except Admins."
          checked={form.values.enforced}
          onChange={(v) => form.setFieldValue("enforced", v)}
        />
        <Field label="Identity provider entity ID" htmlFor="sso-entity" error={errs.idp_entity_id}>
          <Input id="sso-entity" {...bind(form, "idp_entity_id")} />
        </Field>
        <Field label="Identity provider sign-in URL" htmlFor="sso-url" error={errs.idp_sso_url}>
          <Input
            id="sso-url"
            {...bind(form, "idp_sso_url")}
            placeholder="https://login.example.com/saml"
          />
        </Field>
        <Field
          label="Identity provider certificate"
          htmlFor="sso-cert"
          error={errs.idp_cert}
          hint="Paste the PEM certificate."
        >
          <Textarea
            id="sso-cert"
            rows={5}
            {...bind(form, "idp_cert")}
            spellCheck={false}
            className="font-mono"
          />
        </Field>
        {/* Messages that name no field, such as "Turn single sign-on on before requiring it.". */}
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
