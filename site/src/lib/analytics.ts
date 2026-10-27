type Params = Record<string, string | number>;
type AnalyticsWindow = Window & { gtag?: (command: string, name: string, params?: Params) => void };
/** Only site-controlled event names and non-PII parameters are passed by callers. */
export function track(name: string, params: Params = {}) {
  if (location.hostname !== "behindthewatt.com" && location.hostname !== "www.behindthewatt.com") return;
  try {
    const gtag = (window as AnalyticsWindow).gtag;
    if (typeof gtag === "function") gtag("event", name, params);
  } catch { /* Optional analytics must never change an inquiry's delivery result. */ }
}
