import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "ChangeBlast", description: "Cross-repository change impact investigator" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">ChangeBlast</Link>
          <span className="tagline">Evidence-backed change impact across repositories</span>
          <span className="badge ro">read-only</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
