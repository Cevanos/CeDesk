import { FileQuestion } from "lucide-react";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function NotFound() {
  const t = await getTranslations();
  return (
    <main
      id="main"
      className="relative grid min-h-svh place-items-center bg-dots [mask-image:none] px-6"
    >
      <div className="max-w-md rounded-3xl border border-border p-8 text-center shadow-pop glass sm:p-10">
        <div className="ai-frame mx-auto grid size-14 place-items-center rounded-2xl text-primary-ink">
          <FileQuestion className="size-6" aria-hidden />
        </div>
        <p className="mt-4 text-ai-gradient font-mono text-sm font-semibold">404</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">{t("errors.notFoundTitle")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("errors.notFoundDescription")}</p>
        <Link
          href="/tickets"
          className="mt-6 inline-flex h-10 items-center rounded-lg bg-ai-gradient px-5 text-sm font-medium text-white shadow-card hover:brightness-110"
        >
          {t("common.backToTickets")}
        </Link>
      </div>
    </main>
  );
}
