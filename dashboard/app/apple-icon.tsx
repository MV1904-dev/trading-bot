import { ImageResponse } from "next/og";
import { GridMark } from "./icon";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

/** iOS berie apple-touch-icon; bez nej si vyrobí náhľad stránky. */
export default function AppleIcon() {
  return new ImageResponse(<GridMark scale={180 / 512} />, size);
}
