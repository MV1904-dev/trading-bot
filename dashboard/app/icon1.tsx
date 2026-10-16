import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

/**
 * Favicon pre kartu prehliadača. V 16–32 px sa päť čiar zleje, preto len
 * tri hrubé: short, kotva, long. Bez neho prehliadač na PC bral
 * predvolený favicon.ico z create-next-app (čierny trojuholník).
 */
export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%", height: "100%", display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 4,
          background: "#0b0b0e", borderRadius: 7,
        }}
      >
        <div style={{ width: 20, height: 4, borderRadius: 2, background: "#4f9cf5" }} />
        <div style={{ width: 24, height: 4, borderRadius: 2, background: "#f4f4f5" }} />
        <div style={{ width: 20, height: 4, borderRadius: 2, background: "#2fc48d" }} />
      </div>
    ),
    size,
  );
}
