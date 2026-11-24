"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type {
  Article,
  ArticleCategory,
  ArticleStatus,
  ArticleTranslation,
  HelpCenterDomain,
  HelpCenterSettings,
} from "@/lib/api/types";

export function useArticleCategories() {
  return useQuery({
    queryKey: queryKeys.helpCenter.categories,
    queryFn: () => api.get<unknown>("article-categories"),
    select: listOf<ArticleCategory>,
    staleTime: 30_000,
  });
}

export function useSaveArticleCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id?: string; name: string }) =>
      id
        ? api.patch<ArticleCategory>(`article-categories/${id}`, body).then((r) => r.data)
        : api.post<ArticleCategory>("article-categories", body),
    // Articles show their category name, so refresh both lists.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["help-center"] }),
  });
}

export function useDeleteArticleCategory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`article-categories/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["help-center"] }),
  });
}

/** `missingLocale`: only articles that still have no version in that language. */
export function useArticles(missingLocale = "") {
  return useQuery({
    queryKey: [...queryKeys.helpCenter.articles, missingLocale] as const,
    queryFn: () =>
      api.get<unknown>("articles", { limit: 200, missing_locale: missingLocale || undefined }),
    select: listOf<Article>,
    staleTime: 30_000,
  });
}

export interface ArticleInput {
  title: string;
  body: string;
  category_id: string | null;
  status: ArticleStatus;
  slug: string | null;
  meta_title: string | null;
  meta_description: string | null;
  canonical_url: string | null;
}

export function useSaveArticle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<ArticleInput> & { id?: string }) =>
      id
        ? api.patch<Article>(`articles/${id}`, body).then((r) => r.data)
        : api.post<Article>("articles", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.helpCenter.articles }),
  });
}

export function useDeleteArticle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`articles/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.helpCenter.articles }),
  });
}

/* ---------- Languages, SEO and domain ---------- */

export function useHelpCenterSettings() {
  return useQuery({
    queryKey: queryKeys.helpCenter.settings,
    queryFn: async (): Promise<HelpCenterSettings> => {
      const raw = await api.get<Partial<HelpCenterSettings>>("help-center/settings");
      return {
        default_locale: raw?.default_locale ?? "en",
        seo_indexable: raw?.seo_indexable === true,
      };
    },
    staleTime: 60_000,
  });
}

export function useSaveHelpCenterSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: HelpCenterSettings) =>
      api.put<HelpCenterSettings>("help-center/settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["help-center"] }),
  });
}

export function useArticleTranslations(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.helpCenter.translations(id ?? ""),
    queryFn: () => api.get<unknown>(`articles/${id}/translations`),
    select: listOf<ArticleTranslation>,
    enabled: !!id,
    staleTime: 15_000,
  });
}

export type TranslationInput = Omit<
  ArticleTranslation,
  "locale" | "updated_at" | "updated_by_name" | "suggested_slug"
>;

export function useSaveTranslation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    // A PUT makes or replaces the translation, so every field is sent.
    mutationFn: ({ locale, ...body }: TranslationInput & { locale: string }) =>
      api.put<ArticleTranslation>(
        `articles/${id}/translations/${encodeURIComponent(locale)}`,
        body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["help-center"] }),
  });
}

export function useDeleteTranslation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (locale: string) =>
      api.delete<void>(`articles/${id}/translations/${encodeURIComponent(locale)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["help-center"] }),
  });
}

export function useSaveCategoryTranslation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ locale, name }: { locale: string; name: string }) =>
      api.put<unknown>(`article-categories/${id}/translations/${encodeURIComponent(locale)}`, {
        name,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["help-center"] }),
  });
}

export function useDeleteCategoryTranslation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (locale: string) =>
      api.delete<void>(`article-categories/${id}/translations/${encodeURIComponent(locale)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["help-center"] }),
  });
}

/** Custom domain. Off until the server is set up: 403 or 404 means "hide the section". */
export function useHelpCenterDomain() {
  return useQuery({
    queryKey: queryKeys.helpCenter.domain,
    queryFn: () => api.get<HelpCenterDomain | null>("help-center/domain"),
    retry: false,
    meta: { silent: true },
    staleTime: 15_000,
  });
}

export function useSaveHelpCenterDomain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (domain: string) => api.put<HelpCenterDomain>("help-center/domain", { domain }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.helpCenter.domain }),
  });
}

export function useCheckHelpCenterDomain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<HelpCenterDomain>("help-center/domain/check"),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.helpCenter.domain }),
  });
}

export function useRemoveHelpCenterDomain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>("help-center/domain"),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.helpCenter.domain }),
  });
}
