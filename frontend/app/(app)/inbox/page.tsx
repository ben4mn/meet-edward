import { redirect } from "next/navigation";

// The inbox became Today (Sep 2026). Old bookmarks and the previous PWA
// start_url land here; send them on.
export default function InboxRedirect() {
  redirect("/today");
}
