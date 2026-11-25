import type { Metadata } from "next";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { FAQ, LandingPage } from "@/components/landing/landing-page";
import { landingEnabled } from "@/lib/server/landing";
import { SITE_DESCRIPTION, SITE_KEYWORDS, SITE_NAME, SITE_TITLE, siteUrl } from "@/lib/site";

// The one public, indexable page. Everything else stays noindex (see the root layout).
export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: { absolute: SITE_TITLE },
  description: SITE_DESCRIPTION,
  keywords: SITE_KEYWORDS,
  applicationName: SITE_NAME,
  alternates: { canonical: "/" },
  category: "Business software",
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  openGraph: {
    type: "website",
    url: "/",
    siteName: SITE_NAME,
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    locale: "en_US",
  },
  twitter: { card: "summary_large_image", title: SITE_TITLE, description: SITE_DESCRIPTION },
};

/** Structured data for search engines: the organisation, the product and the FAQ. */
function structuredData() {
  const url = siteUrl();
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${url}/#organization`,
        name: "Cevanos",
        url,
        logo: `${url}/logo-mark.png`,
        brand: { "@type": "Brand", name: SITE_NAME },
      },
      {
        "@type": "WebSite",
        "@id": `${url}/#website`,
        url,
        name: SITE_NAME,
        description: SITE_DESCRIPTION,
        inLanguage: "en",
        publisher: { "@id": `${url}/#organization` },
      },
      {
        "@type": "SoftwareApplication",
        "@id": `${url}/#software`,
        name: SITE_NAME,
        url,
        description: SITE_DESCRIPTION,
        applicationCategory: "BusinessApplication",
        applicationSubCategory: "Help desk software",
        operatingSystem: "Web",
        keywords: SITE_KEYWORDS.join(", "),
        publisher: { "@id": `${url}/#organization` },
        featureList: [
          "Shared inbox with saved views and bulk actions",
          "SLA management",
          "Smart ticket routing by team, skill and capacity",
          "Rules, macros and workflows",
          "AI to human handover with summary, reason and sentiment",
          "Customer portal and help center",
          "Email campaigns with suppression and unsubscribe",
          "Reports, dashboards and CSAT",
          "SSO, two-factor authentication and audit log",
        ],
      },
      {
        "@type": "WebPage",
        "@id": `${url}/#webpage`,
        url,
        name: SITE_TITLE,
        isPartOf: { "@id": `${url}/#website` },
        about: { "@id": `${url}/#software` },
        inLanguage: "en",
      },
      {
        "@type": "FAQPage",
        "@id": `${url}/#faq`,
        mainEntity: FAQ.map((f) => ({
          "@type": "Question",
          name: f.q,
          acceptedAnswer: { "@type": "Answer", text: f.a },
        })),
      },
    ],
  };
}

export default async function Home() {
  // Signed-in agents go straight to work; visitors see the landing page.
  if ((await cookies()).get("cedesk_session")?.value) redirect("/tickets");
  // A self-hosted install has no use for our sales page (CEDESK_LANDING=off).
  if (!landingEnabled()) redirect("/login");
  return (
    <>
      <script
        type="application/ld+json"
        // Our own JSON; `<` is escaped so it can never close the script tag early.
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(structuredData()).replace(/</g, "\\u003c"),
        }}
      />
      <LandingPage />
    </>
  );
}
