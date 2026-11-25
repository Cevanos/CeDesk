"use client";

import { Globe, Languages, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { FormError, fieldErrors } from "@/components/workspace/list-shared";
import { useCurrentUser } from "@/hooks/use-auth";
import {
  useArticleTranslations,
  useCheckHelpCenterDomain,
  useDeleteCategoryTranslation,
  useDeleteTranslation,
  useHelpCenterDomain,
  useHelpCenterSettings,
  useRemoveHelpCenterDomain,
  useSaveCategoryTranslation,
  useSaveHelpCenterDomain,
  useSaveHelpCenterSettings,
  useSaveTranslation,
} from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";
import type { Article, ArticleCategory, ArticleStatus, ArticleTranslation } from "@/lib/api/types";
import {
  COMMON_LOCALES,
  isRtl,
  isValidLocale,
  languageName,
  nfc,
  normalizeLocale,
} from "@/lib/locale";
import { timeAgo } from "@/lib/utils";

const OTHER = "__other";

/** A short list of common languages, plus a free text box for any other valid code. */
export function LocalePicker({
  id,
  value,
  onChange,
  exclude = [],
  disabled,
  error,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  exclude?: string[];
  disabled?: boolean;
  error?: string;
}) {
  const common = COMMON_LOCALES.filter((l) => !exclude.includes(l));
  const [custom, setCustom] = useState(() => !!value && !common.includes(value));
  return (
    <div className="space-y-2">
      <Field label="Language" htmlFor={id} error={custom ? undefined : error}>
        <Select
          id={id}
          value={custom ? OTHER : value}
          disabled={disabled}
          options={[
            { value: "", label: "Choose a language…" },
            ...common.map((l) => ({ value: l, label: `${languageName(l)} (${l})` })),
            { value: OTHER, label: "Another language code…" },
          ]}
          onChange={(e) => {
            if (e.target.value === OTHER) {
              setCustom(true);
              onChange("");
            } else {
              setCustom(false);
              onChange(e.target.value);
            }
          }}
        />
      </Field>
      {custom && (
        <Field
          label="Language code"
          htmlFor={`${id}-code`}
          error={error}
          hint="A 2 or 3 letter language, then at most one region or script: hi, pt-br, zh-hans."
        >
          <Input
            id={`${id}-code`}
            value={value}
            disabled={disabled}
            maxLength={12}
            onChange={(e) => onChange(normalizeLocale(e.target.value))}
          />
        </Field>
      )}
    </div>
  );
}

/** Small chips for the languages an article exists in. */
export function LocaleChips({ locales }: { locales?: string[] }) {
  if (!locales || locales.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1" aria-label="Languages">
      {locales.map((l) => (
        <span
          key={l}
          title={languageName(l)}
          className="rounded-md bg-surface-3 px-1.5 py-0.5 text-[11px] font-medium text-foreground-2 uppercase"
        >
          {l}
        </span>
      ))}
    </span>
  );
}

/* ---------- Settings: default language, SEO switch ---------- */

/**
 * The API's 409 reads "N article translation(s) are in 'ar'. Delete them (…) before making it the
 * default." Say it once, with the language's name. Any other 409 text is shown as the API wrote it.
 */
function conflictMessage(apiMessage: string, locale: string) {
  const m = /^(\d+) article translation\(s\) are in '([^']+)'/.exec(apiMessage);
  if (!m) return apiMessage;
  const n = Number(m[1]);
  const lang = languageName(m[2] || locale);
  return `${n} ${n === 1 ? "article has a translation" : "articles have translations"} in ${lang}. Delete ${
    n === 1 ? "it" : "them"
  }, or translate in another language, before making ${lang} the default.`;
}

export function LanguageSettingsCard() {
  const { perms } = useCurrentUser();
  const q = useHelpCenterSettings();
  const save = useSaveHelpCenterSettings();
  const [locale, setLocale] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (q.isPending) return <Skeleton className="h-28" />;
  if (q.isError) return null; // Older server without the routes: nothing to show.

  const canEdit = perms.manageSettings;
  const current = locale ?? q.data.default_locale;
  const dirty = current !== q.data.default_locale;

  const apply = (next: { default_locale: string; seo_indexable: boolean }, done: string) => {
    setError(null);
    save.mutate(next, {
      onSuccess: () => {
        setLocale(null);
        toast.success(done);
      },
      // 409: translations already exist in the new default.
      onError: (e) =>
        setError(
          e instanceof ApiError
            ? e.status === 409
              ? conflictMessage(e.userMessage, next.default_locale)
              : e.userMessage
            : "Could not save.",
        ),
    });
  };

  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-center gap-2">
        <Globe className="size-4 text-primary-ink" aria-hidden />
        <h2 className="text-sm font-semibold">Languages and search engines</h2>
      </div>
      <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,18rem)_auto]">
        <LocalePicker
          id="hc-default-locale"
          value={current}
          disabled={!canEdit}
          onChange={setLocale}
        />
        {canEdit && (
          <Button
            disabled={!dirty || !isValidLocale(current)}
            loading={save.isPending}
            onClick={() => apply({ ...q.data, default_locale: current }, "Default language saved")}
          >
            Save language
          </Button>
        )}
      </div>
      <p className="-mt-2 text-xs text-muted-foreground">
        Articles are written in the default language. Other languages are translations kept beside
        them. Customers see a translation only when both the article and the translation are
        published.
      </p>
      <div className="flex items-start gap-3 border-t border-border pt-3">
        <Switch
          label="Allow search engines"
          checked={q.data.seo_indexable}
          disabled={!canEdit || save.isPending}
          onChange={(v) =>
            apply(
              { default_locale: q.data.default_locale, seo_indexable: v },
              v ? "Search engines allowed" : "Search engines blocked",
            )
          }
        />
        <div>
          <p className="text-sm font-medium">Allow search engines to index the help center</p>
          <p className="text-xs text-muted-foreground">
            Off until you turn it on. While it is off, the public pages ask search engines not to
            index them and the sitemap is empty.
          </p>
        </div>
      </div>
      {!canEdit && (
        <p className="text-xs text-muted-foreground">Only Admins can change these settings.</p>
      )}
      <FormError message={error ?? undefined} />
    </Card>
  );
}

/* ---------- Custom domain (hidden when the server has it off) ---------- */

export function DomainCard() {
  const { perms } = useCurrentUser();
  const q = useHelpCenterDomain();
  const save = useSaveHelpCenterDomain();
  const check = useCheckHelpCenterDomain();
  const remove = useRemoveHelpCenterDomain();
  const { ask, dialog } = useConfirm();
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Server without CEDESK_TLS_ASK_KEY (or an older one): 403 or 404 hides the section.
  if (q.isPending || !perms.manageSettings) return null;
  if (q.isError && q.error instanceof ApiError && [403, 404].includes(q.error.status)) return null;
  if (q.isError) return null;

  const cur = q.data?.domain ? q.data : null;
  const fail = (e: unknown) =>
    setError(e instanceof ApiError ? e.userMessage : "Something went wrong.");

  return (
    <Card className="space-y-3 p-4">
      {dialog}
      <h2 className="text-sm font-semibold">Custom domain</h2>
      {!cur ? (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            save.mutate(domain.trim().toLowerCase(), {
              onSuccess: () => setDomain(""),
              onError: fail,
            });
          }}
        >
          <div className="min-w-60 flex-1">
            <Field label="Domain" htmlFor="hc-domain" hint="For example help.example.com">
              <Input
                id="hc-domain"
                value={domain}
                placeholder="help.example.com"
                onChange={(e) => setDomain(e.target.value)}
              />
            </Field>
          </div>
          <Button type="submit" loading={save.isPending} disabled={!domain.trim()}>
            Add domain
          </Button>
        </form>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{cur.domain}</span>
            <Badge
              tone={
                cur.status === "verified"
                  ? "success"
                  : cur.status === "failed"
                    ? "danger"
                    : "warning"
              }
            >
              {cur.status === "verified"
                ? "Verified"
                : cur.status === "failed"
                  ? "Failed"
                  : "Pending"}
            </Badge>
          </div>
          {cur.status !== "verified" && cur.dns && (
            <div className="rounded-lg border border-border bg-surface-2 p-3">
              <p className="mb-1.5 text-xs text-muted-foreground">
                Publish this DNS record at your domain provider, then press Check.
              </p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[13px]">
                {cur.dns.type && (
                  <>
                    <dt className="text-muted-foreground">Type</dt>
                    <dd>{cur.dns.type}</dd>
                  </>
                )}
                {cur.dns.name && (
                  <>
                    <dt className="text-muted-foreground">Name</dt>
                    <dd className="break-all">{cur.dns.name}</dd>
                  </>
                )}
                {cur.dns.value && (
                  <>
                    <dt className="text-muted-foreground">Value</dt>
                    <dd className="break-all">{cur.dns.value}</dd>
                  </>
                )}
              </dl>
            </div>
          )}
          {cur.last_error && <p className="text-xs text-danger">{cur.last_error}</p>}
          {(cur.failed_checks ?? 0) > 0 && (
            <p className="text-xs text-muted-foreground">Failed checks: {cur.failed_checks}</p>
          )}
          <div className="flex gap-2">
            {cur.status !== "verified" && (
              <Button
                variant="outline"
                loading={check.isPending}
                onClick={() => {
                  setError(null);
                  check.mutate(undefined, { onError: fail });
                }}
              >
                Check
              </Button>
            )}
            <Button
              variant="ghost"
              onClick={() =>
                ask({
                  title: "Remove domain?",
                  message: `${cur.domain} will stop serving the help center.`,
                  confirmLabel: "Remove",
                  run: () => remove.mutate(undefined, { onError: fail }),
                })
              }
            >
              Remove
            </Button>
          </div>
        </div>
      )}
      <FormError message={error ?? undefined} />
    </Card>
  );
}

/* ---------- SEO fields (article and translation) ---------- */

export interface SeoValues {
  slug: string;
  meta_title: string;
  meta_description: string;
  canonical_url: string;
}

export const seoFrom = (a?: Partial<Article & ArticleTranslation> | null): SeoValues => ({
  // The suggested slug is made from the title; null when the title gives none (e.g. Hindi).
  slug: a?.slug ?? a?.suggested_slug ?? "",
  meta_title: a?.meta_title ?? "",
  meta_description: a?.meta_description ?? "",
  canonical_url: a?.canonical_url ?? "",
});

/** Empty text means "not set", which the API wants as null. */
export const seoPayload = (v: SeoValues) => ({
  slug: v.slug.trim() || null,
  meta_title: nfc(v.meta_title.trim()) || null,
  meta_description: nfc(v.meta_description.trim()) || null,
  canonical_url: v.canonical_url.trim() || null,
});

export function SeoFields({
  idPrefix,
  value,
  onChange,
  errs,
}: {
  idPrefix: string;
  value: SeoValues;
  onChange: (v: SeoValues) => void;
  errs: Record<string, string>;
}) {
  const set = (k: keyof SeoValues) => (e: { target: { value: string } }) =>
    onChange({ ...value, [k]: e.target.value });
  return (
    <details className="rounded-lg border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">
        Search engine text (optional)
      </summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field
          label="Page address ending (slug)"
          htmlFor={`${idPrefix}-slug`}
          error={errs.slug}
          hint="Only for looks. The article id decides the page, so changing it breaks no link."
        >
          <Input id={`${idPrefix}-slug`} value={value.slug} onChange={set("slug")} />
        </Field>
        <Field
          label="Canonical address"
          htmlFor={`${idPrefix}-canon`}
          error={errs.canonical_url}
          hint="https only. Leave empty to use the page's own address."
        >
          <Input
            id={`${idPrefix}-canon`}
            type="url"
            value={value.canonical_url}
            onChange={set("canonical_url")}
          />
        </Field>
        <Field label="Meta title" htmlFor={`${idPrefix}-mt`} error={errs.meta_title}>
          <Input
            id={`${idPrefix}-mt`}
            maxLength={120}
            value={value.meta_title}
            onChange={set("meta_title")}
          />
        </Field>
        <Field label="Meta description" htmlFor={`${idPrefix}-md`} error={errs.meta_description}>
          <Textarea
            id={`${idPrefix}-md`}
            rows={2}
            maxLength={320}
            value={value.meta_description}
            onChange={set("meta_description")}
          />
        </Field>
      </div>
    </details>
  );
}

/* ---------- Languages panel on an article ---------- */

export function LanguagesPanel({
  article,
  defaultLocale,
}: {
  article: Article;
  defaultLocale: string;
}) {
  const { perms } = useCurrentUser();
  const q = useArticleTranslations(article.id);
  const del = useDeleteTranslation(article.id);
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<ArticleTranslation | "new" | null>(null);
  const canWrite = perms.manageSettings;
  const count = q.data?.length ?? 0;

  return (
    <section aria-labelledby="langs-h" className="mt-6 space-y-3 border-t border-border pt-4">
      {dialog}
      <div className="flex flex-wrap items-center gap-2">
        <Languages className="size-4 text-primary-ink" aria-hidden />
        <h3 id="langs-h" className="text-sm font-semibold">
          Languages
        </h3>
        <span className="text-xs text-muted-foreground">
          Written in {languageName(defaultLocale)}. Translations show only while the article is
          published.
        </span>
        {canWrite && editing === null && (
          <Button
            size="sm"
            variant="outline"
            className="ms-auto"
            disabled={count >= 40}
            onClick={() => setEditing("new")}
          >
            <Plus className="size-3.5" /> Add language
          </Button>
        )}
      </div>

      {q.isPending ? (
        <Skeleton className="h-16" />
      ) : q.isError ? (
        <p role="alert" className="text-sm text-danger">
          {q.error instanceof ApiError ? q.error.userMessage : "Could not load translations."}
        </p>
      ) : count === 0 ? (
        <p className="text-sm text-muted-foreground">No translations yet.</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {q.data.map((t) => (
            <li key={t.locale} className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-sm">
              <span className="font-medium">
                {languageName(t.locale)}{" "}
                <span className="text-xs text-muted-foreground">({t.locale})</span>
              </span>
              <Badge tone={t.status === "published" ? "success" : "warning"}>
                {t.status === "published" ? "Published" : "Draft"}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {t.updated_by_name && `${t.updated_by_name} · `}
                {t.updated_at && timeAgo(t.updated_at)}
              </span>
              {canWrite && (
                <span className="ms-auto flex gap-1">
                  <Button size="sm" variant="outline" onClick={() => setEditing(t)}>
                    <Pencil className="size-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete ${languageName(t.locale)} translation`}
                    onClick={() =>
                      ask({
                        title: "Delete translation?",
                        message: `The ${languageName(t.locale)} version will be removed. Customers will see ${languageName(defaultLocale)} instead.`,
                        confirmLabel: "Delete",
                        run: () =>
                          del.mutate(t.locale, {
                            onSuccess: () => toast.success("Translation deleted"),
                          }),
                      })
                    }
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {!canWrite && (
        <p className="text-xs text-muted-foreground">Only Admins can add or change translations.</p>
      )}

      {editing !== null && (
        <TranslationForm
          key={editing === "new" ? "new" : editing.locale}
          article={article}
          existing={editing === "new" ? null : editing}
          taken={[defaultLocale, ...(q.data?.map((t) => t.locale) ?? [])]}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  );
}

function TranslationForm({
  article,
  existing,
  taken,
  onClose,
}: {
  article: Article;
  existing: ArticleTranslation | null;
  taken: string[];
  onClose: () => void;
}) {
  const save = useSaveTranslation(article.id);
  const [locale, setLocale] = useState(existing?.locale ?? "");
  const [title, setTitle] = useState(existing?.title ?? "");
  const [body, setBody] = useState(existing?.body ?? "");
  const [status, setStatus] = useState<ArticleStatus>(existing?.status ?? "draft");
  const [seo, setSeo] = useState(() => seoFrom(existing));
  const [errs, setErrs] = useState<Record<string, string>>({});
  const rtl = isRtl(locale);

  return (
    <form
      noValidate
      className="space-y-3 rounded-lg border border-border bg-surface-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!isValidLocale(locale))
          next.locale = "Choose a language, or type a code like hi or pt-br";
        if (!title.trim()) next.title = "Enter a title";
        if (!body.trim()) next.body = "Write the translation";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          { locale, title: nfc(title.trim()), body: nfc(body), status, ...seoPayload(seo) },
          {
            onSuccess: () => {
              toast.success(status === "published" ? "Translation published" : "Translation saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <LocalePicker
          id="tr-locale"
          value={locale}
          disabled={!!existing}
          exclude={existing ? [] : taken}
          onChange={setLocale}
          error={errs.locale}
        />
        <Field label="Status" htmlFor="tr-status">
          <Select
            id="tr-status"
            value={status}
            options={[
              { value: "draft", label: "Draft" },
              { value: "published", label: "Published" },
            ]}
            onChange={(e) => setStatus(e.target.value as ArticleStatus)}
          />
        </Field>
      </div>
      <Field label="Title" htmlFor="tr-title" error={errs.title}>
        <Input
          id="tr-title"
          maxLength={200}
          value={title}
          dir={rtl ? "rtl" : undefined}
          onChange={(e) => setTitle(e.target.value)}
        />
      </Field>
      <Field label="Article (Markdown)" htmlFor="tr-body" error={errs.body}>
        <Textarea
          id="tr-body"
          rows={10}
          maxLength={100_000}
          dir={rtl ? "rtl" : undefined}
          className="font-mono text-[13px] leading-6"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </Field>
      <SeoFields idPrefix="tr" value={seo} onChange={setSeo} errs={errs} />
      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save translation
        </Button>
      </div>
    </form>
  );
}

/* ---------- Category names in other languages ---------- */

export function CategoryTranslations({ category }: { category: ArticleCategory }) {
  const { perms } = useCurrentUser();
  const save = useSaveCategoryTranslation(category.id);
  const del = useDeleteCategoryTranslation(category.id);
  const [locale, setLocale] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const names = Object.entries(category.name_translations ?? {});
  const canWrite = perms.manageSettings;

  return (
    <section aria-labelledby="cat-langs" className="space-y-2 border-t border-border pt-3">
      <h3 id="cat-langs" className="text-sm font-semibold">
        Name in other languages
      </h3>
      {names.length === 0 && <p className="text-xs text-muted-foreground">None yet.</p>}
      <ul className="space-y-1">
        {names.map(([l, n]) => (
          <li key={l} className="flex items-center gap-2 text-sm">
            <span className="text-xs text-muted-foreground">{languageName(l)}</span>
            <span lang={l} dir={isRtl(l) ? "rtl" : undefined} className="font-medium">
              {n}
            </span>
            {canWrite && (
              <Button
                size="sm"
                variant="ghost"
                className="ms-auto"
                aria-label={`Remove ${languageName(l)} name`}
                onClick={() => del.mutate(l)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            )}
          </li>
        ))}
      </ul>
      {canWrite ? (
        <div className="space-y-2">
          <LocalePicker
            id="cat-tr-locale"
            value={locale}
            onChange={setLocale}
            exclude={Object.keys(category.name_translations ?? {})}
          />
          <Field label="Name" htmlFor="cat-tr-name" error={err ?? undefined}>
            <Input
              id="cat-tr-name"
              maxLength={80}
              value={name}
              dir={isRtl(locale) ? "rtl" : undefined}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Button
            size="sm"
            variant="outline"
            loading={save.isPending}
            disabled={!isValidLocale(locale) || !name.trim()}
            onClick={() => {
              setErr(null);
              save.mutate(
                { locale, name: nfc(name.trim()) },
                {
                  onSuccess: () => {
                    toast.success("Name saved");
                    setLocale("");
                    setName("");
                  },
                  onError: (e) => setErr(e instanceof ApiError ? e.userMessage : "Could not save."),
                },
              );
            }}
          >
            Add name
          </Button>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Only Admins can change these names.</p>
      )}
    </section>
  );
}
