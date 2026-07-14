import { mirrorText } from "../../../lib/api";
export const prerender = true;
export const GET = mirrorText("fleet.csv", "text/csv; charset=utf-8");
