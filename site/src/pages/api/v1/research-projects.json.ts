import { json } from "../../../lib/api";
import { projects, researchReviewed } from "../../../lib/research";
export const prerender = true;
export const GET = () => json({license:"CC BY 4.0",reviewed_at:researchReviewed,evidence_basis:"Selected primary-source research; not added to the reviewed operating fleet. Field-specific sources and unknowns retained.",projects});
