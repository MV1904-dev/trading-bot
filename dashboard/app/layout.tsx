import type { Metadata, Viewport } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "Trading bot",
  description: "cTrader grid bot — stav, pozície, výkon",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#09090b",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="sk">
      {/* pb-24 nechá miesto pre spodnú navigáciu na mobile */}
      <body>
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
