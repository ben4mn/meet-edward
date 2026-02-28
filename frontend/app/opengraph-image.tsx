import { ImageResponse } from "next/og";

export const runtime = "nodejs";
export const alt = "Edward — Your AI Assistant That Remembers Everything";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "linear-gradient(135deg, #0f172a 0%, #0d1f2d 50%, #0f172a 100%)",
          position: "relative",
        }}
      >
        {/* Radial glow */}
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            width: 800,
            height: 600,
            borderRadius: "50%",
            background:
              "radial-gradient(circle, rgba(82, 183, 136, 0.12) 0%, transparent 60%)",
          }}
        />

        {/* Edward avatar SVG inline */}
        <div
          style={{
            display: "flex",
            marginBottom: 40,
          }}
        >
          <svg
            width="160"
            height="160"
            viewBox="0 0 100 100"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <circle cx="50" cy="50" r="50" fill="#52b788" />
            <g transform="translate(50,50) scale(0.82) translate(-50,-50)">
              <rect x="25" y="32" width="50" height="40" rx="8" fill="#FFFFFF" />
              <rect x="28" y="35" width="44" height="34" rx="6" fill="#0d1117" />
              <circle cx="40" cy="52" r="4" fill="#52b788" />
              <circle cx="60" cy="52" r="4" fill="#52b788" />
              <rect x="48" y="20" width="4" height="14" rx="1" fill="#FFFFFF" />
              <circle cx="50" cy="18" r="4" fill="#52b788" stroke="#FFFFFF" strokeWidth="1.5" />
              <rect x="42" y="62" width="16" height="2" rx="1" fill="#52b788" fillOpacity="0.8" />
            </g>
          </svg>
        </div>

        {/* Title */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div
            style={{
              fontSize: 56,
              fontWeight: 700,
              color: "#f1f5f9",
              fontFamily: "monospace",
              letterSpacing: "-0.02em",
              textAlign: "center",
              lineHeight: 1.1,
            }}
          >
            Edward
          </div>
          <div
            style={{
              fontSize: 26,
              color: "#94a3b8",
              textAlign: "center",
              maxWidth: 600,
              lineHeight: 1.4,
            }}
          >
            Your AI assistant that remembers everything.
          </div>
        </div>

        {/* Feature pills */}
        <div
          style={{
            display: "flex",
            gap: 12,
            marginTop: 36,
          }}
        >
          {["Memory", "Scheduling", "Messaging", "Code"].map(
            (label) => (
              <div
                key={label}
                style={{
                  padding: "8px 18px",
                  borderRadius: 20,
                  border: "1px solid rgba(82, 183, 136, 0.3)",
                  color: "#52b788",
                  fontSize: 15,
                  fontFamily: "monospace",
                  background: "rgba(82, 183, 136, 0.08)",
                }}
              >
                {label}
              </div>
            )
          )}
        </div>

      </div>
    ),
    {
      ...size,
    }
  );
}
