import { json } from "../../../lib/api";
import { loadMirror } from "../../../lib/mirror";
import changes from "../../../data/reviewed-changes.json";
import { sourceLinkedEvents } from "../../../lib/research-events";
export const prerender = true;
export const GET = async () => {
 const mirror = await loadMirror();
 return json({license:"CC BY 4.0",interpretation_reviewed_at:"2026-10-27",mirror_exported_at:mirror.summary.as_of,note:"Interpreted entries are manually reviewed and ordered by event date. Filing metadata is explicitly distinguished from completed document-content review. Published events with a source URL and existing dossier refresh with the mirror. Registry candidates are excluded; neither list measures current delivered power.",reviewed_changes:changes,published_events:sourceLinkedEvents(mirror.facilities.facilities, mirror.events.events)});
};
