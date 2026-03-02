import { DocsSidebar } from "../../components/docs/DocsSidebar";
import { DocsBreadcrumbJsonLd } from "../../components/docs/DocsBreadcrumbJsonLd";

export const metadata = {
  title: "Docs — Edward",
  description: "Documentation for Edward, the open-source AI assistant with long-term memory.",
};

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[#0f172a] text-[#f1f5f9] flex">
      <DocsBreadcrumbJsonLd />
      <DocsSidebar />
      <main className="flex-1 min-w-0 lg:pl-0">{children}</main>
    </div>
  );
}
