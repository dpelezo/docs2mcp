import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "docs2mcp playground",
  description:
    "API docs to task-shaped MCP servers, and a four-way agent eval against OpenAPI conversion and FastMCP.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site">
          <nav>
            <Link className="brand" href="/">docs2mcp</Link>
            <Link href="/apis/pokeapi">Tools</Link>
            <Link href="/tasks">Tasks</Link>
            <a href="https://github.com/dpelezo/docs2mcp">GitHub</a>
            <a href="https://pypi.org/project/docs2mcp-cli/">PyPI</a>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
