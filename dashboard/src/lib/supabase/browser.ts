"use client";

import { createBrowserClient } from "@supabase/ssr";

// Singleton browser client used for Realtime subscriptions on dashboard pages.
let _client: ReturnType<typeof createBrowserClient> | null = null;

export function supabaseBrowser() {
  if (_client) return _client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY env."
    );
  }
  _client = createBrowserClient(url, anonKey);
  return _client;
}
