export function sourceLinkedEvents(facilities: {slug:string}[], events: Record<string,any>[]) {
  const slugs = new Set(facilities.map(f => f.slug));
  return events.filter(event => slugs.has(event.facility) && typeof event.source_url === "string" && event.source_url.startsWith("https://"));
}
