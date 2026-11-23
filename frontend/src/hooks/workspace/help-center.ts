"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, listOf } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { Article, ArticleCategory, ArticleStatus } from "@/lib/api/types";

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

export function useArticles() {
  return useQuery({
    queryKey: queryKeys.helpCenter.articles,
    queryFn: () => api.get<unknown>("articles", { limit: 200 }),
    select: listOf<Article>,
    staleTime: 30_000,
  });
}

export interface ArticleInput {
  title: string;
  body: string;
  category_id: string | null;
  status: ArticleStatus;
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
