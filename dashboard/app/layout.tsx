import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "Trading bot",
  description: "cTrader grid bot — stav, pozície, výkon",
  appleWebApp: { capable: true, title: "Bot", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#141416" },
  ],
  viewportFit: "cover",
};

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
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_INIT}
        </Script>
        <Nav />
        <div className="mx-auto w-full max-w-3xl px-4 pb-24 sm:pb-8">
          {children}
        </div>
      </body>
    </html>
  );
}
