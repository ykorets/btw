import texasProjects from "../data/texas-projects.json";
import ohioProjects from "../data/ohio-projects.json";
export { texasProjects, ohioProjects };
export const projects = [...texasProjects.map(p => ({...p, state:"TX", state_name:"Texas"})), ...ohioProjects];
export const researchReviewed = "2026-10-27";
export function projectPath(slug: string) { return `/projects/${slug}/`; }
export function sourceFor(project: typeof projects[number], id: string) {
  const source = project.sources.find(s => s.id === id);
  if (!source) throw new Error(`Unresolved project source: ${project.slug}/${id}`);
  return source;
}
