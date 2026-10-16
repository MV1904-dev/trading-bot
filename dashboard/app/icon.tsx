import { ImageResponse } from "next/og";

export const size = { width: 512, height: 512 };
export const contentType = "image/png";

/**
 * Ikona sa generuje pri builde, takže v repe nie sú binárne súbory.
 * Motív je mriežka gridu: úrovne nad kotvou (shorty, modré) a pod ňou
 * (longy, zelené), uprostred kotva s bodom kurzu — rovnaký jazyk ako
 * pás pozícií na Mape. Pre kartu prehliadača je zjednodušená icon1.tsx.
 */
export default function Icon() {
  return new ImageResponse(<GridMark scale={1} />, size);
}

const SHORT = "#4f9cf5", LONG = "#2fc48d", INK = "#f4f4f5";

export function GridMark({ scale }: { scale: number }) {
  const s = (v: number) => v * scale;
  // Päť úrovní namiesto siedmich: v 60 px na ploche telefónu sa sedem
  // tenkých čiar zlievalo do šedej plochy.
  const levels = [-2, -1, 0, 1, 2];
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: s(40),
        background: "radial-gradient(circle at 50% 30%, #23232a 0%, #0b0b0e 70%)",
        borderRadius: s(112),
      }}
    >
      {levels.map((l) =>
        l === 0 ? (
          <div key={l} style={{ display: "flex", position: "relative", width: s(340), height: s(28) }}>
            <div style={{ width: "100%", height: s(28), borderRadius: 999, background: INK }} />
            {/* bod kurzu na kotve */}
            <div
              style={{
                position: "absolute", left: s(196), top: s(-24), width: s(76), height: s(76),
                borderRadius: 999, background: INK, border: `${s(14)}px solid #0f0f13`,
              }}
            />
          </div>
        ) : (
          <div
            key={l}
            style={{
              width: s(Math.abs(l) === 1 ? 300 : 236),
              height: s(32),
              borderRadius: 999,
              background: l < 0 ? SHORT : LONG,
              opacity: Math.abs(l) === 1 ? 1 : 0.55,
            }}
          />
        ),
      )}
    </div>
  );
}
