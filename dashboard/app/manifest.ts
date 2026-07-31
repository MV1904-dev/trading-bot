import type { MetadataRoute } from "next";

/**
 * Bez manifestu sa „Pridať na plochu" správa ako záložka — otvorí sa
 * v prehliadači s adresným riadkom. S ním beží aplikácia v standalone
 * režime, teda na celú obrazovku a s vlastnou ikonou.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Trading bot",
    short_name: "Bot",
    description: "cTrader grid bot — stav, pozície, výkon",
    start_url: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#09090b",
    theme_color: "#09090b",
    lang: "sk",
    icons: [
      { src: "/icon", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/apple-icon", sizes: "180x180", type: "image/png" },
    ],
  };
}
