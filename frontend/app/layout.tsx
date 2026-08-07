import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
  weight: ["300", "400", "500", "600", "700", "800"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "AE-03 Orchestrator — Unified Agent Form",
  description:
    "Dynamic multi-agent orchestration engine with real-time DAG execution, " +
    "observability, and human-in-the-loop approval. Compile natural-language " +
    "goals into executable agent graphs.",
  keywords: [
    "agent orchestrator",
    "multi-agent",
    "DAG execution",
    "LLM",
    "AI workflow",
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
