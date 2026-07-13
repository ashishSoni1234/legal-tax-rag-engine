import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "LexRAG — Legal & Tax AI Research Assistant",
  description:
    "AI-powered retrieval-augmented generation for US tax law and legal document research. Hybrid search across Acts, Judgments, POV papers, and Tax documents.",
  keywords: ["legal AI", "tax law", "RAG", "document search", "US tax"],
  authors: [{ name: "LexRAG" }],
  openGraph: {
    title: "LexRAG — Legal & Tax AI Research Assistant",
    description: "AI-powered legal and tax document research with hybrid search and citation graphs.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="antialiased">{children}</body>
    </html>
  );
}
