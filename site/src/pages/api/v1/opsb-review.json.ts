import { json } from "../../../lib/api";
import review from "../../../data/opsb-review-2026-10.json";
export const prerender = true;
export const GET = () => json({license:"CC BY 4.0",...review});
