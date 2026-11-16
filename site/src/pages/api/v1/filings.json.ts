import { json } from "../../../lib/api";
import { loadMirrorJsonOptional } from "../../../lib/mirror";
export const prerender = true;
export async function GET() {
  return json(await loadMirrorJsonOptional("filings.json", {
    license: "CC BY 4.0",
    classification: "registry_record_not_btw_verified",
    filings: [],
  }));
}
