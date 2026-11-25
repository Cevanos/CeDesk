import { ImageResponse } from "next/og";

export const alt = "CeDesk: AI help desk software for customer support teams";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/** The social-share card (Open Graph / Twitter), drawn at build time. */
export default function OpengraphImage() {
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: 72,
        color: "white",
        background:
          "radial-gradient(900px 500px at 90% -10%, rgba(119,98,251,0.7), transparent 60%), radial-gradient(700px 480px at 0% 110%, rgba(47,123,255,0.45), transparent 62%), #1a1240",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", fontSize: 44, fontWeight: 700 }}>
        Ce<span style={{ color: "#b3a6ff" }}>Desk</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{ fontSize: 76, fontWeight: 700, lineHeight: 1.05, letterSpacing: -2 }}>
          AI help desk software for support teams
        </div>
        <div style={{ marginTop: 28, fontSize: 32, color: "rgba(255,255,255,0.75)" }}>
          Shared inbox · SLAs · Routing · Automation · Help center
        </div>
      </div>
      <div style={{ fontSize: 26, color: "#c9bfff" }}>
        People and AI resolve customer conversations together
      </div>
    </div>,
    size,
  );
}
