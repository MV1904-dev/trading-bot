import { ImageResponse } from "next/og";

export const size = { width: 512, height: 512 };
export const contentType = "image/png";

/**
 * Ikona sa generuje pri builde, takže v repe nie sú binárne súbory.
 * Motív je mriežka gridu: úrovne nad referenciou (shorty) a pod ňou
 * (longy), zvýraznená stredná kotva.
 */
export default function Icon() {
  return new ImageResponse(<GridMark scale={1} />, size);
}

export function GridMark({ scale }: { scale: number }) {
  const levels = [-3, -2, -1, 0, 1, 2, 3];
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 26 * scale,
        background: "#09090b",
        borderRadius: 96 * scale,
      }}
    >
      {levels.map((l) => (
        <div
          key={l}
          style={{
            width: l === 0 ? 300 * scale : 220 * scale,
            height: (l === 0 ? 18 : 12) * scale,
            borderRadius: 999,
            background:
              l === 0 ? "#f4f4f5" : l < 0 ? "#38bdf8" : "#34d399",
            opacity: l === 0 ? 1 : 1 - Math.abs(l) * 0.18,
          }}
        />
      ))}
    </div>
  );
}
