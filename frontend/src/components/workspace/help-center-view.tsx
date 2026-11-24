"use client";

import {
  BookOpen,
  Eye,
  FolderPlus,
  Pencil,
  Plus,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Markdown } from "@/components/public/markdown";
import { useConfirm } from "@/components/ui/confirm";
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
import {
  CategoryTranslations,
  DomainCard,
  LanguageSettingsCard,
  LanguagesPanel,
  LocaleChips,
  SeoFields,
  seoFrom,
  seoPayload,
} from "@/components/workspace/help-center-languages";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import {
  useArticleCategories,
  useArticles,
  useDeleteArticle,
  useDeleteArticleCategory,
  useHelpCenterSettings,
  useSaveArticle,
  useSaveArticleCategory,
} from "@/hooks/use-workspace";
import type { Article, ArticleCategory, ArticleStatus } from "@/lib/api/types";
import { COMMON_LOCALES, languageName, nfc } from "@/lib/locale";
import { cn, timeAgo } from "@/lib/utils";

/** Share of "yes" votes as a whole percent, or null when nobody voted. */
export function helpfulPercent(yes = 0, no = 0): number | null {
  const total = yes + no;
  return total === 0 ? null : Math.round((yes / total) * 100);
}

const ALL = "";
const STATUS_FILTER = [
  { value: "", label: "All statuses" },
  { value: "published", label: "Published" },
  { value: "draft", label: "Drafts" },
];

export function HelpCenterView() {
  const categories = useArticleCategories();
  const [missing, setMissing] = useState("");
  const articles = useArticles(missing);
  const settings = useHelpCenterSettings();
  const defaultLocale = settings.data?.default_locale ?? "en";
  const delArticle = useDeleteArticle();
  const delCategory = useDeleteArticleCategory();
  const saveArticle = useSaveArticle();
  const { ask, dialog } = useConfirm();

  const [category, setCategory] = useState(ALL);
  const [status, setStatus] = useState("");
  const [editingArticle, setEditingArticle] = useState<Article | "new" | null>(null);
  const [editingCategory, setEditingCategory] = useState<ArticleCategory | "new" | null>(null);

  if (categories.isPending || (articles.isPending && !articles.isPlaceholderData))
    return <Skeleton className="h-72" />;
  if (categories.isError) return <LoadError error={categories.error} />;
  if (articles.isError) return <LoadError error={articles.error} />;

  const cats = categories.data;
  const all = articles.data ?? [];
  const countIn = (id: string) => all.filter((a) => a.category_id === id).length;
  const nameOf = (id: string | null) => cats.find((c) => c.id === id)?.name ?? "No category";
  const shown = all.filter(
    (a) => (category === ALL || a.category_id === category) && (!status || a.status === status),
  );

  return (
    <div className="space-y-4">
      {dialog}
      <p className="text-sm text-muted-foreground">
        Articles customers find on your public help center. Only published articles are visible;
        drafts stay private.
      </p>

      <LanguageSettingsCard />
      <DomainCard />

      <div className="grid gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside aria-label="Categories">
          <Card className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-border px-3.5 py-2.5">
              <h2 className="text-sm font-semibold">Categories</h2>
              <Button size="sm" variant="ghost" onClick={() => setEditingCategory("new")}>
                <FolderPlus className="size-3.5" /> Add
              </Button>
            </div>
            <ul className="p-1.5">
              <CategoryRow
                label="All articles"
                count={all.length}
                active={category === ALL}
                onSelect={() => setCategory(ALL)}
              />
              {cats.map((c) => (
                <CategoryRow
                  key={c.id}
                  label={c.name}
                  count={countIn(c.id)}
                  active={category === c.id}
                  onSelect={() => setCategory(c.id)}
                  onEdit={() => setEditingCategory(c)}
                  onDelete={() =>
                    ask({
                      title: "Delete category?",
                      message: `“${c.name}” will be deleted. Its ${countIn(c.id)} articles stay, without a category.`,
                      confirmLabel: "Delete",
                      run: () =>
                        delCategory.mutate(c.id, {
                          onSuccess: () => {
                            if (category === c.id) setCategory(ALL);
                            toast.success("Category deleted");
                          },
                        }),
                    })
                  }
                />
              ))}
            </ul>
          </Card>
        </aside>

        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="w-44">
              <Select
                aria-label="Filter by status"
                value={status}
                options={STATUS_FILTER}
                onChange={(e) => setStatus(e.target.value)}
              />
            </div>
            <div className="w-52">
              <Select
                aria-label="Needs a language"
                value={missing}
                options={[
                  { value: "", label: "Needs a language: any" },
                  ...COMMON_LOCALES.filter((l) => l !== defaultLocale).map((l) => ({
                    value: l,
                    label: `Needs ${languageName(l)}`,
                  })),
                ]}
                onChange={(e) => setMissing(e.target.value)}
              />
            </div>
            <Button className="ml-auto" onClick={() => setEditingArticle("new")}>
              <Plus className="size-4" /> New article
            </Button>
          </div>

          {shown.length === 0 ? (
            <Card>
              <EmptyState icon={<BookOpen className="size-6" />} title="No articles here yet">
                Write the answers to the questions customers ask most, so they can help themselves.
              </EmptyState>
            </Card>
          ) : (
            <Card className="overflow-hidden">
              <ul className="divide-y divide-border">
                {shown.map((a) => {
                  const pct = helpfulPercent(a.helpful_count, a.not_helpful_count);
                  return (
                    <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3.5">
                      <div className="min-w-0 flex-1 basis-60">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{a.title}</span>
                          <Badge tone={a.status === "published" ? "success" : "warning"}>
                            {a.status === "published" ? "Published" : "Draft"}
                          </Badge>
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                          <LocaleChips locales={a.locales} />
                          {a.category_name ?? nameOf(a.category_id)}
                          {a.updated_at && ` · Updated ${timeAgo(a.updated_at)}`}
                        </div>
                      </div>
                      <Helpful yes={a.helpful_count ?? 0} no={a.not_helpful_count ?? 0} pct={pct} />
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={saveArticle.isPending}
                        onClick={() =>
                          saveArticle.mutate(
                            {
                              id: a.id,
                              status: a.status === "published" ? "draft" : "published",
                            },
                            {
                              onSuccess: () =>
                                toast.success(
                                  a.status === "published" ? "Moved to drafts" : "Published",
                                ),
                            },
                          )
                        }
                      >
                        {a.status === "published" ? "Unpublish" : "Publish"}
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setEditingArticle(a)}>
                        <Pencil className="size-3.5" /> Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={delArticle.isPending}
                        aria-label={`Delete ${a.title}`}
                        onClick={() =>
                          ask({
                            title: "Delete article?",
                            message: `“${a.title}” will be removed from the help center.`,
                            confirmLabel: "Delete",
                            run: () =>
                              delArticle.mutate(a.id, {
                                onSuccess: () => toast.success("Article deleted"),
                              }),
                          })
                        }
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
        </div>
      </div>

      <Dialog
        open={editingArticle !== null}
        onClose={() => setEditingArticle(null)}
        title={editingArticle === "new" ? "New article" : "Edit article"}
        className="sm:max-w-5xl"
      >
        {editingArticle !== null && (
          <>
            <ArticleForm
              key={editingArticle === "new" ? "new" : editingArticle.id}
              article={editingArticle === "new" ? null : editingArticle}
              categories={cats}
              defaultCategory={category}
              onClose={() => setEditingArticle(null)}
            />
            {editingArticle !== "new" && (
              <LanguagesPanel article={editingArticle} defaultLocale={defaultLocale} />
            )}
          </>
        )}
      </Dialog>

      <Dialog
        open={editingCategory !== null}
        onClose={() => setEditingCategory(null)}
        title={editingCategory === "new" ? "Add category" : "Rename category"}
      >
        {editingCategory !== null && (
          <CategoryForm
            key={editingCategory === "new" ? "new" : editingCategory.id}
            category={
              editingCategory === "new"
                ? null
                : (cats.find((c) => c.id === editingCategory.id) ?? editingCategory)
            }
            onClose={() => setEditingCategory(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

function CategoryRow({
  label,
  count,
  active,
  onSelect,
  onEdit,
  onDelete,
}: {
  label: string;
  count: number;
  active: boolean;
  onSelect: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  return (
    <li
      className={cn(
        "group flex items-center gap-1 rounded-lg pr-1 transition-colors",
        active ? "bg-primary-soft" : "hover:bg-surface-2",
      )}
    >
      <button
        type="button"
        aria-pressed={active}
        onClick={onSelect}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left text-sm font-medium",
          active ? "text-primary-ink" : "text-foreground-2",
        )}
      >
        <span className="truncate">{label}</span>
        <span className="ml-auto text-xs text-muted-foreground">{count}</span>
      </button>
      {onEdit && (
        <button
          type="button"
          aria-label={`Rename ${label}`}
          onClick={onEdit}
          className="grid size-7 place-items-center rounded-md text-muted-foreground opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 hover:bg-surface-3 hover:text-foreground"
        >
          <Pencil className="size-3.5" />
        </button>
      )}
      {onDelete && (
        <button
          type="button"
          aria-label={`Delete ${label}`}
          onClick={onDelete}
          className="grid size-7 place-items-center rounded-md text-muted-foreground opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 hover:bg-surface-3 hover:text-danger"
        >
          <Trash2 className="size-3.5" />
        </button>
      )}
    </li>
  );
}

/** Thumbs up / down numbers with a bar for the share of "yes". */
function Helpful({ yes, no, pct }: { yes: number; no: number; pct: number | null }) {
  return (
    <div
      className="w-36"
      title={pct === null ? "No votes yet" : `${pct}% found this helpful`}
      aria-label={pct === null ? "No votes yet" : `${yes} helpful, ${no} not helpful`}
    >
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <ThumbsUp className="size-3.5 text-success" aria-hidden /> {yes}
        </span>
        <span className="inline-flex items-center gap-1">
          <ThumbsDown className="size-3.5 text-danger" aria-hidden /> {no}
        </span>
        <span className="ml-auto font-medium text-foreground-2">
          {pct === null ? "–" : `${pct}%`}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3">
        <div
          className="h-full rounded-full bg-success transition-[width]"
          style={{ width: `${pct ?? 0}%` }}
        />
      </div>
    </div>
  );
}

function CategoryForm({
  category,
  onClose,
}: {
  category: ArticleCategory | null;
  onClose: () => void;
}) {
  const save = useSaveArticleCategory();
  const [name, setName] = useState(category?.name ?? "");
  const [errs, setErrs] = useState<Record<string, string>>({});
  return (
    <div className="space-y-4">
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return setErrs({ name: "Enter a name" });
          save.mutate(
            { id: category?.id, name: nfc(name.trim()) },
            {
              onSuccess: () => {
                toast.success("Category saved");
                onClose();
              },
              onError: (err) => setErrs(fieldErrors(err)),
            },
          );
        }}
      >
        <Field label="Name" htmlFor="cat-name" error={errs.name}>
          <Input
            id="cat-name"
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <FormError message={errs._form} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        </div>
      </form>
      {category && <CategoryTranslations category={category} />}
    </div>
  );
}

function ArticleForm({
  article,
  categories,
  defaultCategory,
  onClose,
}: {
  article: Article | null;
  categories: ArticleCategory[];
  defaultCategory: string;
  onClose: () => void;
}) {
  const save = useSaveArticle();
  const [title, setTitle] = useState(article?.title ?? "");
  const [body, setBody] = useState(article?.body ?? "");
  const [categoryId, setCategoryId] = useState(article?.category_id ?? defaultCategory);
  const [status, setStatus] = useState<ArticleStatus>(article?.status ?? "draft");
  const [seo, setSeo] = useState(() => seoFrom(article));
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!title.trim()) next.title = "Enter a title";
        if (!body.trim()) next.body = "Write the article";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          {
            id: article?.id,
            title: nfc(title.trim()),
            body: nfc(body),
            category_id: categoryId || null,
            status,
            ...seoPayload(seo),
          },
          {
            onSuccess: () => {
              toast.success(status === "published" ? "Article published" : "Draft saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_12rem_10rem]">
        <Field label="Title" htmlFor="art-title" error={errs.title}>
          <Input
            id="art-title"
            maxLength={200}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Field>
        <Field label="Category" htmlFor="art-cat" error={errs.category_id}>
          <Select
            id="art-cat"
            value={categoryId}
            options={[
              { value: "", label: "No category" },
              ...categories.map((c) => ({ value: c.id, label: c.name })),
            ]}
            onChange={(e) => setCategoryId(e.target.value)}
          />
        </Field>
        <Field label="Status" htmlFor="art-status">
          <Select
            id="art-status"
            value={status}
            options={[
              { value: "draft", label: "Draft" },
              { value: "published", label: "Published" },
            ]}
            onChange={(e) => setStatus(e.target.value as ArticleStatus)}
          />
        </Field>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Field
          label="Article (Markdown)"
          htmlFor="art-body"
          error={errs.body}
          hint="Use # for headings, **bold**, - for lists and [text](https://…) for links. HTML is not allowed."
        >
          <Textarea
            id="art-body"
            rows={16}
            className="font-mono text-[13px] leading-6"
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </Field>
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-[13px] font-medium text-foreground-2">
            <Eye className="size-3.5" aria-hidden /> Preview
          </p>
          <div
            aria-live="polite"
            className="max-h-[26rem] min-h-40 overflow-y-auto rounded-lg border border-border bg-background p-4"
          >
            {body.trim() ? (
              <Markdown source={body} />
            ) : (
              <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>
            )}
          </div>
        </div>
      </div>

      <SeoFields idPrefix="art" value={seo} onChange={setSeo} errs={errs} />

      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          {status === "published" ? "Save and publish" : "Save draft"}
        </Button>
      </div>
    </form>
  );
}
