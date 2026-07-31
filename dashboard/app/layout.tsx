import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "Trading bot",
  description: "cTrader grid bot — stav, pozície, výkon",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

/**
 * Beží pred prvým vykreslením, takže stránka nikdy nebliká v zlej téme.
 * Uložená voľba má prednosť pred systémom; try/catch kvôli prehliadačom
 * so zakázaným localStorage (inak by výnimka zhodila celý skript).
 */
const THEME_INIT = `
try {
  var t = localStorage.getItem('theme');
  var dark = t ? t === 'dark'
                : matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.classList.toggle('dark', dark);
} catch (e) {}
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="sk" suppressHydrationWarning>
      <body>
        {/* next/script s beforeInteractive namiesto obyčajného <script> —
            ten React pri klientskom renderi nespúšťa a hlási to ako chybu
            v konzole. */}
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_INIT}
        </Script>
        {/* Nav je v DOM pred obsahom, aby na desktope sedel hore; na mobile
            je fixed na spodku, takže na poradí tam nezáleží. */}
        <Nav />
        <div className="mx-auto min-h-dvh w-full max-w-5xl px-4 pb-24 pt-4 sm:pb-8">
          {children}
        </div>
      </body>
    </html>
  );
}
