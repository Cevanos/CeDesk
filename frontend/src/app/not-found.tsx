import { FileQuestion } from "lucide-react";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function NotFound() {
  const t = await getTranslations();
  return (
    <main id="main" className="grid min-h-svh place-items-center px-6">
      <div className="max-w-md text-center">
        <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-primary-soft text-primary-ink">
          <FileQuestion className="size-6" aria-hidden />
        </div>
        <p className="mt-4 font-mono text-xs text-subtle-foreground">404</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">{t("errors.notFoundTitle")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("errors.notFoundDescription")}</p>
        <Link
          href="/tickets"
          className="mt-6 inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary-hover"
        >
          {t("common.backToTickets")}
        </Link>
      </div>
    </main>
  );
}
