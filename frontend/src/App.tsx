import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { TopNav } from "./components/TopNav";
import { Projects } from "./pages/Projects";
import { NewProject } from "./pages/NewProject";
import { ProjectOverview } from "./pages/ProjectOverview";
import { ProjectChecks } from "./pages/ProjectChecks";
import { ProjectTickets } from "./pages/ProjectTickets";
import { ProjectSettings } from "./pages/ProjectSettings";
import { Connectors } from "./pages/Connectors";
import { CheckDetail } from "./pages/CheckDetail";
import { NewCheck } from "./pages/NewCheck";
import { EditCheck } from "./pages/EditCheck";

/**
 * Routes mirror the hierarchy: projects own checks and tickets, so a check
 * lives at /projects/:slug/checks/:id rather than at a flat /checks/:id. The
 * URL then always says where you are, and every level above is reachable by
 * trimming the path.
 *
 * Connectors stay outside the project tree because one connector serves many
 * projects.
 */
export default function App() {
  return (
    <BrowserRouter>
      <TopNav />
      <Routes>
        <Route path="/" element={<Projects />} />
        <Route path="/projects/new" element={<NewProject />} />
        <Route path="/projects/:slug" element={<ProjectOverview />} />
        <Route path="/projects/:slug/checks" element={<ProjectChecks />} />
        <Route path="/projects/:slug/checks/new" element={<NewCheck />} />
        <Route path="/projects/:slug/checks/:id" element={<CheckDetail />} />
        <Route path="/projects/:slug/checks/:id/edit" element={<EditCheck />} />
        <Route path="/projects/:slug/tickets" element={<ProjectTickets />} />
        <Route path="/projects/:slug/settings" element={<ProjectSettings />} />

        <Route path="/connectors" element={<Connectors />} />

        {/* Pre-projects URLs. Bookmarks and the old flat routes should not 404. */}
        <Route path="/checks/*" element={<Navigate to="/" replace />} />
        <Route path="/tickets" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
