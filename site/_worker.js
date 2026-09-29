const RETIRED = new Set(["/providers/akamai-linode", "/providers/contabo", "/providers/hetzner-cloud", "/providers/namecheap", "/providers/vultr"]);

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/";
    if (RETIRED.has(path)) {
      return new Response("This provider page is no longer available because no current source-verified plan data can be published.\n", {
        status: 410,
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "Cache-Control": "public, max-age=300",
          "X-Robots-Tag": "noindex",
        },
      });
    }
    return env.ASSETS.fetch(request);
  },
};
